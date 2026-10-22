"""A read chart into the payloads of its claims (ADR 0116, sections 1 and 2).

`chart_claims(song)` reads one chart (`codec.Song`) into the four payloads
ADR 0116 names for a chart document: `chord_sequence` version 2, `form`
version 1, untimed `meter_layers` version 2 and `key_sequence` version 1
(none when the key field is empty). It returns payloads only; the
references (the document, the run, and the form and sequence the meter
pins) are the importer's, since only it knows the revisions.

Structure the reader cannot read raises `chart.UnsupportedChart`, and so does
a chart whose reading World's checks refuse, so no chart is ever read in
part. A chord symbol the grammar cannot read is a text-only reading, counted
in `ChartClaims.text_only`.

The rules this module adds to the reader's, all named in `PARAMETERS`:

- **Sections.** A new section starts at the first bar, at each rehearsal
  mark, each `{`, each ending, after each repeat's last bar, at the segno, at
  the coda, and after each bar a jump, the Fine or the first coda sign
  leaves from. A section with no mark of its own repeats the label (and
  kind) of the mark in force; an ending is labelled `1.`, `2.`, `3.`; before
  the first mark the label is `unmarked` (ADR 0116, decision U).
- **Cells.** A chord in cell k of a bar of n cells starts k/n of the bar
  after the downbeat (ADR 0116's placement rule, to be checked against
  iReal Pro's playback).
- **Bar repeats.** `x` repeats the bar before and `r` the two before (the
  `r` bar and the empty bar after it): written out, a repeated bar whose only
  chord is on its downbeat and is the chord still sounding lists no chords
  (held), and otherwise each chord is a new entry copying the repeated
  entry's readings.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from chart import Bar, Chart, UnsupportedChart, read_chart
from symbols import GRAMMAR_VERSION, read_key, reading_of

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from world.form import check_form, check_meter_layers_v2  # noqa: E402
from world.harmony import HarmonyError, check_chord_sequence_v2  # noqa: E402
from world.tonality import TonalityError, check_key_sequence  # noqa: E402

METHOD = "ireal-chart"
UNMARKED = "unmarked"
MARK_KINDS = {"i": "intro", "v": "verse"}

# Every choice the grammar makes, on the record as the run's parameters.
PARAMETERS: dict[str, Any] = {
    "method": METHOD,
    "grammar_version": GRAMMAR_VERSION,
    "cell_placement": "a chord in cell k (0-based) of a bar of n cells starts k/n of the bar after the downbeat",
    "unmarked_section_label": UNMARKED,
    "sections": "a section starts at the first bar, each rehearsal mark, each {, each ending, after each repeat's "
                "last bar, at the segno and the coda, and after the bar a jump, the Fine or the first coda sign "
                "leaves from; one with no mark of its own repeats the mark in force; endings are 1., 2., 3.",
    "bar_repeats": "x and r are written out: a repeated bar whose only chord is on its downbeat and is the chord "
                   "still sounding lists no chords (held); otherwise each chord is a new entry copying the "
                   "repeated entry's readings",
    "implied_extensions": "an extension implies the sevenths and ninths below it; a thirteenth leaves out the "
                          "eleventh; 7alt and 11 name uncertain tones",
    "minor_key": "natural minor",
}

# Placeholders for the references the importer adds; World's checks need a
# well-formed pin to count roles, never the real revision.
_STAND_IN = {"id": "urn:uuid:00000000-0000-4000-8000-000000000000", "revision_id": "sha256:" + "0" * 64}


@dataclass
class ChartClaims:
    sequence: dict[str, Any]
    form: dict[str, Any]
    meter: dict[str, Any]
    key: dict[str, Any] | None
    text_only: list[tuple[str, str]] = field(default_factory=list)   # (symbol, reason)


@dataclass
class _Chord:
    offset: Fraction            # of the bar
    readings: list[dict[str, Any]]


def _entry_readings(cell, previous: dict[str, Any] | None,
                    text_only: list[tuple[str, str]]) -> list[dict[str, Any]]:
    readings = []
    reading, reason = reading_of(cell.text, previous)
    if reason:
        text_only.append((cell.text, reason))
    readings.append(reading)
    if cell.alternate is not None:
        alternate, reason = reading_of(cell.alternate, previous)
        if reason:
            text_only.append((cell.alternate, reason))
        if alternate != reading:
            readings.append(alternate)
    return readings


def _bar_chords(chart: Chart, text_only: list[tuple[str, str]]) -> list[list[_Chord]]:
    """Each written bar's chords with their readings, bar repeats written out."""
    out: list[list[_Chord]] = []
    previous: dict[str, Any] | None = None   # the previous entry's first reading
    pending_second: list[_Chord] | None = None
    for bar in chart.bars:
        cells = bar.chord_cells()
        kinds = {cell.kind for _, cell in cells}
        if pending_second is not None:
            if cells:
                raise UnsupportedChart(f"bar {bar.number} after a two-bar repeat is not empty")
            out.append(pending_second)
            if pending_second:
                previous = pending_second[-1].readings[0]
            pending_second = None
            continue
        if kinds & {"repeat_one", "repeat_two"}:
            if len(cells) != 1:
                raise UnsupportedChart(f"a bar repeat beside other cells (bar {bar.number})")
            back = 1 if cells[0][1].kind == "repeat_one" else 2
            if len(out) < back:
                raise UnsupportedChart(f"a bar repeat with too few bars before it (bar {bar.number})")
            out.append(out[-back])
            if back == 2:
                pending_second = out[-2]
            if out[-1]:
                previous = out[-1][-1].readings[0]
            continue
        chords = []
        for index, cell in cells:
            if cell.kind == "slash":
                continue
            readings = _entry_readings(cell, previous, text_only)
            chords.append(_Chord(bar.offset(index), readings))
            previous = readings[0]
        out.append(chords)
    if pending_second is not None:
        out.append(pending_second)
        if len(out) > len(chart.bars):
            raise UnsupportedChart("a two-bar repeat at the last bar")
    return out


def _section_starts(chart: Chart) -> list[int]:
    count = len(chart.bars)
    starts = {1}
    for bar in chart.bars:
        if bar.mark or bar.opens == "{" or bar.ending:
            starts.add(bar.number)
    for repeat in chart.repeats:
        starts.add(repeat.first)
        starts.add(repeat.last + 1)
        for ending in repeat.endings:
            starts.add(ending.first)
            starts.add(ending.last + 1)
    for number in (chart.segno, chart.coda):
        if number is not None:
            starts.add(number)
    for number in (chart.coda_from, chart.fine, *[jump.at for jump in chart.jumps]):
        if number is not None:
            starts.add(number + 1)
    return sorted(start for start in starts if start <= count)


def _form(chart: Chart) -> tuple[dict[str, Any], list[int]]:
    """The form payload, and each bar's section index."""
    starts = _section_starts(chart)
    sections, of_bar = [], []
    label, kind = UNMARKED, None
    for bar in chart.bars:
        if bar.number in starts:
            if bar.mark:
                label, kind = bar.mark, MARK_KINDS.get(bar.mark)
            section: dict[str, Any] = {"label": f"{bar.ending}." if bar.ending else label}
            if kind and not bar.ending:
                section["kind"] = kind
            section["locator"] = {"bar": bar.number}
            sections.append(section)
        of_bar.append(len(sections) - 1)

    def starting(number: int) -> int:
        index = of_bar[number - 1]
        if sections[index]["locator"]["bar"] != number:
            raise UnsupportedChart(f"no section starts at bar {number}")
        return index

    def ending_at(number: int) -> int:
        index = of_bar[number - 1]
        if number < len(of_bar) and of_bar[number] == index:
            raise UnsupportedChart(f"no section ends at bar {number}")
        return index

    payload: dict[str, Any] = {"sections": sections}
    repeats = []
    for repeat in chart.repeats:
        # With endings, the reader's `last` is the bar of the last `}`; the
        # repeat runs to the end of its last ending.
        last = repeat.endings[-1].last if repeat.endings else repeat.last
        item: dict[str, Any] = {"first": starting(repeat.first), "last": ending_at(last), "times": repeat.times}
        if repeat.endings:
            item["endings"] = [{"first": starting(e.first), "last": ending_at(e.last), "passes": [e.number]}
                               for e in repeat.endings]
        repeats.append(item)
    if repeats:
        payload["repeats"] = sorted(repeats, key=lambda r: (r["first"], -r["last"]))
    jumps = []
    for jump in chart.jumps:
        item = {"after": ending_at(jump.at), "kind": jump.kind, "then": jump.then or "end"}
        if jump.kind == "dal_segno":
            item["to"] = starting(chart.segno)
        if jump.then == "coda":
            item["coda_from"] = ending_at(chart.coda_from)
            item["coda"] = starting(chart.coda)
        elif jump.then == "fine":
            item["fine"] = ending_at(chart.fine)
        jumps.append(item)
    if jumps:
        payload["jumps"] = jumps
    return payload, of_bar


def _at(offset: Fraction, bar: Bar) -> dict[str, int] | None:
    """An offset (a fraction of the bar) as ADR 0086's written length."""
    length = offset * Fraction(*bar.time)
    if length == 0:
        return None
    return {"count": length.numerator, "unit": length.denominator}


def chart_claims(markup: str, key_field: str) -> ChartClaims:
    """Read one chart's markup and key field into its claims' payloads.
    Raises UnsupportedChart."""
    chart = read_chart(markup)
    text_only: list[tuple[str, str]] = []
    bar_chords = _bar_chords(chart, text_only)
    form, of_bar = _form(chart)
    entries: list[dict[str, Any]] = []
    bars = []
    for bar, chords, section in zip(chart.bars, bar_chords, of_bar):
        numerator, denominator = bar.time
        item: dict[str, Any] = {"numerator": numerator, "denominator": denominator,
                                "locator": {"bar": bar.number}, "section": section}
        listed = []
        # A repeated bar holds only the chord still sounding: `x` after a
        # one-chord bar does, but `r` over two different one-chord bars
        # brings back a chord that is not sounding, a new entry.
        held = (len(chords) == 1 and chords[0].offset == 0 and bool(entries)
                and chords[0].readings == entries[-1]["readings"]
                and any(chords is earlier for earlier in bar_chords[:bar.number - 1]))
        if not held:
            for chord in chords:
                listed.append({"entry": len(entries)} | ({"at": at} if (at := _at(chord.offset, bar)) else {}))
                entries.append({"readings": chord.readings})
        if listed:
            item["chords"] = listed
        bars.append(item)
    if not entries:
        raise UnsupportedChart("no chords")
    sequence = {"entries": entries}
    meter = {"layers": [{"role": "written", "bars": bars}]}
    key_reading = read_key(key_field)
    key = {"regions": [{"readings": [key_reading]}]} if key_reading is not None else None
    _check(sequence, form, meter, key)
    return ChartClaims(sequence, form, meter, key, text_only)


def _check(sequence: dict, form: dict, meter: dict, key: dict | None) -> None:
    """World's checks over the payloads, with stand-in references: a reading
    World refuses is a chart this grammar cannot read, never a partial one."""
    document = {"role": "document", **_STAND_IN}
    run = {"role": "run", **_STAND_IN}
    try:
        sequence_claim = {"payload": sequence, "refs": [document, run]}
        check_chord_sequence_v2(sequence_claim)
        form_claim = {"payload": form, "refs": [document, run]}
        check_form(form_claim)
        check_meter_layers_v2({"payload": meter, "refs": [document, run, {"role": "form", **_STAND_IN},
                                                          {"role": "sequence", **_STAND_IN}]},
                              form_claim=form_claim, sequence_claim=sequence_claim)
        if key is not None:
            check_key_sequence({"payload": key, "refs": [document, run]})
    except (HarmonyError, TonalityError) as error:
        raise UnsupportedChart(f"World refuses the reading: {error}") from None
