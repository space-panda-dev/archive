"""A stand-in for the import stage's reading of a chart into claims (ADR 0116,
sections 1 and 2), so the exporter's round trip can be tested before the
importer lands. The importer's own claim builder replaces it on merge; until
then this module is the reading the exporter inverts, and every choice it
makes that the ADR leaves open is named here:

- **Section starts.** At a rehearsal mark, a `{`, an ending, a segno and the
  coda (the second `Q`); and after a `}`, the bar whose staff text states a
  jump, the bar carrying `<Fine>` when a jump ends there, the first `Q` (leave for the coda after
  this bar) and the last bar of a repeat's last ending, so every place a jump
  or an ending lands or leaves is a section boundary, as form v1 needs.
- **Labels.** A section's label is the rehearsal mark in force (`unmarked`
  before the first); the section an ending starts with is `1.`, `2.`, `3.`;
  kind `intro` for `*i` and `verse` for `*v`.
- **Bar repeats.** `x` copies the bar before it, `r` the two bars before it
  over itself and the empty bar after it. A copied bar whose only chord is on
  its downbeat and is the chord already sounding lists no chords (held);
  otherwise each copied chord is a new entry with the copied readings.
- **A bar with no chord** lists none and holds the chord before it.
- **Alternates.** An attached `(chord)` is the entry's second reading, read
  with the entry's own reading as the chord before it (for `(W/X)`).
- **Directions.** The importer's rule (`claims.chart_directions`): the style
  and tempo as the first bar's first direction, each comment a direction on
  its bar.

`read(song)` gives the payloads; `records(...)` wraps them, with a Work, a
composer credit and a lead sheet, as World's records an exporter is given.
Standard library, World and the importer's direction rule only.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from world.contributions import check_contribution  # noqa: E402
from world.form import check_form, check_meter_layers_v3  # noqa: E402
from world.harmony import check_chord_sequence_v2  # noqa: E402
from world.lead_sheets import check_lead_sheet  # noqa: E402
from world.tonality import check_key_sequence  # noqa: E402

from chart import Chart, UnsupportedChart, read_chart  # noqa: E402
from claims import chart_directions  # noqa: E402
from symbols import read_key, reading_of  # noqa: E402

UNMARKED = "unmarked"
KINDS = {"i": "intro", "v": "verse"}


def _at(offset: Fraction) -> dict[str, int]:
    return {"count": offset.numerator, "unit": offset.denominator}


def section_starts(chart: Chart) -> set[int]:
    """The bar numbers at which a form section starts."""
    last = len(chart.bars)
    starts = {1}
    after = set()
    for bar in chart.bars:
        if bar.mark or bar.opens == "{" or bar.ending is not None or bar.segno:
            starts.add(bar.number)
        if bar.closes == "}":
            after.add(bar.number)
    if chart.coda is not None:
        starts.add(chart.coda)
    after |= {jump.at for jump in chart.jumps}
    if any(jump.then == "fine" for jump in chart.jumps):
        after.add(chart.fine)
    if chart.coda_from is not None:
        after.add(chart.coda_from)
    after |= {repeat.endings[-1].last for repeat in chart.repeats if repeat.endings}
    return starts | {n + 1 for n in after if n < last}


def _form(chart: Chart) -> tuple[dict[str, Any], dict[int, int]]:
    starts = section_starts(chart)
    ending_at = {ending.first: ending.number for repeat in chart.repeats for ending in repeat.endings}
    sections: list[dict[str, Any]] = []
    section_of: dict[int, int] = {}
    mark = None
    for bar in chart.bars:
        if bar.mark:
            mark = bar.mark
        if bar.number in starts:
            if bar.number in ending_at:
                section = {"label": f"{ending_at[bar.number]}."}
            else:
                section = {"label": mark or UNMARKED}
                if mark in KINDS:
                    section["kind"] = KINDS[mark]
            section["locator"] = {"bar": bar.number}
            sections.append(section)
        section_of[bar.number] = len(sections) - 1
    payload: dict[str, Any] = {"sections": sections}
    repeats = []
    for repeat in chart.repeats:
        first = section_of[repeat.first]
        if repeat.endings:
            last = section_of[repeat.endings[-1].last]
            endings = [{"first": section_of[e.first], "last": section_of[e.last], "passes": [e.number]}
                       for e in repeat.endings]
            repeats.append({"first": first, "last": last, "times": repeat.times, "endings": endings})
        else:
            repeats.append({"first": first, "last": section_of[repeat.last], "times": repeat.times})
    if repeats:
        payload["repeats"] = repeats
    jumps = []
    for jump in chart.jumps:
        item: dict[str, Any] = {"after": section_of[jump.at], "kind": jump.kind, "then": jump.then or "end"}
        if jump.kind == "dal_segno":
            item["to"] = section_of[chart.segno]
        if jump.then == "fine":
            item["fine"] = section_of[chart.fine]
        if jump.then == "coda":
            item["coda_from"] = section_of[chart.coda_from]
            item["coda"] = section_of[chart.coda]
        jumps.append(item)
    if jumps:
        payload["jumps"] = jumps
    return payload, section_of


def _bars(chart: Chart, section_of: dict[int, int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    bars: list[dict[str, Any]] = []
    effective: list[list[tuple[Fraction, list[dict[str, Any]]]]] = []
    held: list[dict[str, Any]] | None = None
    previous: dict[str, Any] | None = None       # the entry before's first reading, for `W/X`
    second: list[tuple[Fraction, list[dict[str, Any]]]] | None = None   # the bar an `r` copies next
    for bar in chart.bars:
        length = Fraction(*bar.time)
        cells = bar.chord_cells()
        kinds = [cell.kind for _, cell in cells]
        copied = None
        if "repeat_one" in kinds or "repeat_two" in kinds:
            if len(kinds) != 1:
                raise UnsupportedChart(f"a bar repeat beside chords (bar {bar.number})")
            back = 1 if kinds[0] == "repeat_one" else 2
            if len(effective) < back:
                raise UnsupportedChart(f"a bar repeat with no bar to repeat (bar {bar.number})")
            copied = effective[-back]
            second = effective[-1] if back == 2 else None
        elif not [kind for kind in kinds if kind != "slash"] and second is not None:
            copied, second = second, None
        else:
            second = None
        listed: list[tuple[Fraction, list[dict[str, Any]]]]
        if copied is not None:
            held_only = len(copied) == 1 and copied[0][0] == 0 and copied[0][1] == held
            listed = [] if held_only else copy.deepcopy(copied)
        else:
            listed = []
            for index, cell in cells:
                if cell.kind == "slash":
                    continue
                if cell.kind == "no_chord":
                    readings = [{"no_chord": True, "text": "n"}]
                else:
                    first, _ = reading_of(cell.text, previous)
                    readings = [first]
                    if cell.alternate:
                        other, _ = reading_of(cell.alternate, first)
                        if other != first:
                            readings.append(other)
                listed.append((bar.offset(index) * length, readings))
                previous = readings[0]
        out: dict[str, Any] = {"numerator": bar.time[0], "denominator": bar.time[1],
                               "locator": {"bar": bar.number}, "section": section_of[bar.number]}
        chords = []
        for offset, readings in listed:
            entries.append({"readings": readings})
            chord: dict[str, Any] = {"entry": len(entries) - 1}
            if offset:
                chord["at"] = _at(offset)
            chords.append(chord)
        if chords:
            out["chords"] = chords
            held = listed[-1][1]
            previous = held[0]
        bars.append(out)
        effective.append(listed if listed else ([(Fraction(0), held)] if held is not None else []))
    return entries, bars


def read(markup: str, key_field: str, style: str = "", tempo: str = "") -> dict[str, Any]:
    """The payloads a chart's markup, key field, style and tempo field read
    into: `sequence` (chord_sequence v2), `form` (form v1), `meter` (untimed
    meter_layers v3, one written layer, with its directions) and `key`
    (key_sequence v1, or None for an empty key field). Raises
    UnsupportedChart."""
    chart = read_chart(markup)
    form, section_of = _form(chart)
    entries, bars = _bars(chart, section_of)
    for number, directions in chart_directions(chart, style, tempo).items():
        bars[number - 1]["directions"] = directions
    if not entries:
        raise UnsupportedChart("no chords")
    key = read_key(key_field)
    return {
        "sequence": {"entries": entries},
        "form": form,
        "meter": {"layers": [{"role": "written", "bars": bars}]},
        "key": {"regions": [{"readings": [key]}]} if key is not None else None,
    }


# ---------------------------------------------------------------------------
# Records, as World gives them to an exporter


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _urn(seed: str) -> str:
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"urn:uuid:{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _record(seed: str, shape: str, payload: dict[str, Any], refs: list[dict[str, Any]],
            contract: tuple[str, str] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {"id": _urn(seed), "shape": shape, "payload": payload, "refs": refs,
                              "agent": _urn("agent"), "contract": list(contract) if contract else None}
    if shape == "entity":
        record["kind"] = payload["kind"]
    record["revision_id"] = "sha256:" + hashlib.sha256(_canonical(record)).hexdigest()
    record["object_id"] = record["id"]
    return record


def _ref(role: str, record: dict[str, Any]) -> dict[str, str]:
    return {"role": role, "id": record["id"], "revision_id": record["revision_id"]}


def _claim(record: dict[str, Any]) -> dict[str, Any]:
    return {"payload": record["payload"], "refs": record["refs"], "revision_id": record["revision_id"]}


def records(title: str, composer: str, payloads: dict[str, Any], seed: str = "chart") \
        -> tuple[list[dict[str, Any]], dict[str, str]]:
    """World's records of one read chart linked to a Work titled `title`, with
    a composer credit (ADR 0116, section 4: role composer, the composer field
    verbatim as `text` and `credited_as`, a confirmed person as contributor)
    and a person's lead sheet pinning them, each checked with World's checks;
    and the lead sheet's pin, which an export renders."""
    document = _record(seed + "/document", "artifact", {"content": {"digest": "sha256:" + "0" * 64}}, [])
    doc = _ref("document", document)
    sequence = _record(seed + "/sequence", "claim", payloads["sequence"], [doc],
                       ("archive.claim/chord_sequence", "2"))
    form = _record(seed + "/form", "claim", payloads["form"], [doc], ("archive.claim/form", "1"))
    meter = _record(seed + "/meter", "claim", payloads["meter"],
                    [doc, _ref("form", form), _ref("sequence", sequence)], ("archive.claim/meter_layers", "3"))
    check_chord_sequence_v2(_claim(sequence))
    check_form(_claim(form))
    check_meter_layers_v3(_claim(meter), form_claim=_claim(form), sequence_claim=_claim(sequence))
    work = _record(seed + "/work", "entity", {"kind": "musical_work", "label": title}, [])
    made = [document, sequence, form, meter, work]
    refs = [_ref("work", work), _ref("chords", sequence), _ref("meter", meter), _ref("form", form)]
    if payloads["key"] is not None:
        key = _record(seed + "/key", "claim", payloads["key"], [doc], ("archive.claim/key_sequence", "1"))
        check_key_sequence(_claim(key))
        made.append(key)
        refs.append(_ref("key", key))
    if composer:
        person = _record(seed + "/composer", "entity", {"kind": "person", "label": composer}, [])
        credit = _record(seed + "/credit", "claim",
                         {"scope": "work", "role": {"type": "composer"}, "text": composer, "credited_as": composer},
                         [_ref("work", work), doc, _ref("contributor", person)], ("archive.claim/contribution", "1"))
        made += [person, credit]
        check_contribution(_claim(credit), _targets(made))
        refs.append(_ref("credits", credit))
    lead = _record(seed + "/lead", "claim", {"label": "iReal"}, refs, ("archive.claim/lead_sheet", "2"))
    check_lead_sheet({**_claim(lead), "agent": lead["agent"]}, _targets(made), {lead["agent"]: "person"},
                     version="2")
    made.append(lead)
    return made, {"id": lead["id"], "revision_id": lead["revision_id"]}


def _targets(made: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """What each record is, as World's checks read their targets."""
    return {record["revision_id"]: record for record in made}
