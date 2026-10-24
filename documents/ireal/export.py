"""Exporter `lead-sheet-ireal` version 1 (ADR 0116, section 5): one
untimed lead sheet revision of a Work as an iReal Pro link of one chart.

It is a view of exact revisions, as ADR 0089's exports are: the relay runs it
as `export.py <input.json> <output> <losses.json>` (`relay/exporting.py`),
the input holding the request and the records it renders as World reads them,
and it writes the link and the list of what it lost. Nothing it writes is a
claim; only a file a person sends is admitted, under `archive.claim/export`
version 2 (specification 0.0.11).

What it reads, all through the lead sheet's pins:

- the bars: the first `written` layer of the first untimed meter claim
  (meter layers version 2 or 3: bar lengths, time signatures, the sections
  bars fall in, and each chord's offset); the chord sequence and form are
  the ones that meter pins;
- the directions (meter layers version 3, ADR 0116 section 3): the first
  bar's first direction is the style field, and its tempo, when iReal can
  write it, the tempo field (the importer's rule, inverted); every other
  direction is written as a comment `<text>` on its bar;
- the chords: each entry's first reading as the chord, its second as the
  alternate `(chord)`; a reading keeps its verbatim `text` when that reads
  under grammar version 1 to the same stack, else the grammar table's first
  quality whose classes give the stack, spelled by the key (ADR 0080);
- the form: rehearsal marks, `{` `}` repeats with `N1`..`N3` endings and a
  `<Nx>` count, segno `S`, coda `Q`, `<D.C./D.S. al Coda/Fine>`, `<Fine>`;
- the key field from the first key region's first reading; the composer
  from the composer credits (`Composer Unknown` with none, since an empty
  field would write iReal's song separator); the title from the Work's
  label; options `style`, `tempo` and `transpose` (semitones, ADR 0089's
  transposition). An option overrides the first direction; with neither,
  the style is `Medium Swing` and the tempo 0 (iReal's default).

A chord at offset o of a bar of length L goes in cell k of n with k/n = o/L
(the importer's placement rule, inverted); n is 4 when that places every
chord, else the least that does. A bar that would read back as the bar
before it is written `x`.

**Lost, listed in the losses:** readings after the second; weights;
uncertain tones and stacks no quality writes (`7alt` and `11` are written);
voicings; a reading with no root; text-only readings whose text is not an
iReal chord token (both written as a comment over an empty cell); every
layer but the first written one; groupings and modulations; time
signatures iReal has no code for; key regions and readings after the first,
and keys that are neither major nor natural minor; labels that are not an
iReal mark where a mark is needed (written as a comment); section kinds
other than intro and verse; jumps with `repeats_on_return`, more than one
jump, more than three endings, endings that are not one pass each, nested
repeats; melody, lyrics and underlays; chord claims other than the one the
meter indexes; credits in a role other than composer; a first direction's
tempo that is fractional, outside iReal's 40 to 300, or counted in a beat
other than the first bar's denominator; tempos of other directions; and
direction text iReal would read as a jump, Fine or repeat count, or that
holds `<` or `>`.

Where it differs from the ADR's wording: a text-only reading whose text is a
single chord token of iReal's markup (the chart's own symbol, such as `C2`
or `C**`) is written back as that token, not as a comment, since iReal wrote
it and reads it; this keeps the round trip equal.
"""

from __future__ import annotations

import html
import json
import re
import sys
from fractions import Fraction
from math import lcm
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from world.exports import transpose_key, transpose_reading  # noqa: E402
from world.form import check_form, check_meter_layers_v3, is_untimed_meter  # noqa: E402
from world.harmony import check_chord_sequence_v2  # noqa: E402
from world.tonality import check_key_sequence, key_pitch_names  # noqa: E402

import codec  # noqa: E402
from chart import _CHORD, _INVISIBLE, TIME_SIGNATURES  # noqa: E402
from symbols import FLAT_NAMES, UnsupportedSymbol, read_key, read_symbol, symbol_of  # noqa: E402

EXPORTER = {"id": "lead-sheet-ireal", "version": "1"}
FORMAT = "ireal"
MEDIA_TYPE = "text/plain"
SUFFIX = ".irealb"
OPTIONS = {"style", "tempo", "transpose"}
DEFAULT_STYLE = "Medium Swing"
NO_COMPOSER = "Composer Unknown"

LEAD_SHEET = {("archive.claim/lead_sheet", "1"), ("archive.claim/lead_sheet", "2")}
METERS = {("archive.claim/meter_layers", "2"), ("archive.claim/meter_layers", "3")}
KEY_SEQUENCE = ("archive.claim/key_sequence", "1")
MARKS = {"A": None, "B": None, "C": None, "D": None, "i": "intro", "v": "verse"}
UNMARKED = "unmarked"
MAX_CELLS = 16
CODES = {value: code for code, value in TIME_SIGNATURES.items()}
_TOKEN = re.compile(rf"(?:{_CHORD}|{_INVISIBLE})\Z")
_STAFF = re.compile(r"D\.[CS]\.|Fine\Z|\d{1,2}x\Z", re.IGNORECASE)
_NAME = re.compile(r"[A-G][b#]?\Z")


class ExportRefused(ValueError):
    """A request this exporter cannot render; the message says why."""


# ---------------------------------------------------------------------------
# Reading the records


class _Records:
    def __init__(self, records: list[dict[str, Any]]):
        self.by_revision = {record["revision_id"]: record for record in records}

    def get(self, ref: dict[str, Any], what: str) -> dict[str, Any]:
        record = self.by_revision.get(ref["revision_id"])
        if record is None:
            raise ExportRefused(f"the {what} {ref['revision_id']} is not among the records given")
        return record


def _contract(record: dict[str, Any]) -> tuple[str, str] | None:
    contract = record.get("contract")
    return (contract[0], contract[1]) if contract else None


def _role(record: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [ref for ref in record.get("refs", []) if ref.get("role") == role]


def _claim(record: dict[str, Any]) -> dict[str, Any]:
    """A record as World's claim checks take it."""
    return {"payload": record["payload"], "refs": record["refs"], "revision_id": record["revision_id"]}


def _check_options(options: dict[str, Any]) -> tuple[str | None, int | None, int]:
    unknown = set(options) - OPTIONS
    if unknown:
        raise ExportRefused(f"unknown options {sorted(unknown)}; this exporter takes {sorted(OPTIONS)}")
    style = options.get("style")
    tempo = options.get("tempo")
    transpose = options.get("transpose", 0)
    if style is not None and (not isinstance(style, str) or not style or "=" in style):
        raise ExportRefused("style must be text without '='")
    if tempo is not None and (isinstance(tempo, bool) or not isinstance(tempo, int)
                              or not (tempo == 0 or 40 <= tempo <= 300)):
        raise ExportRefused("tempo must be 0 (iReal's default) or 40 to 300 beats a minute")
    if isinstance(transpose, bool) or not isinstance(transpose, int) or not -12 <= transpose <= 12:
        raise ExportRefused("transpose must be an integer from -12 to 12 semitones")
    return style, tempo, transpose


# ---------------------------------------------------------------------------
# Symbols


def _names(key: dict[str, Any] | None) -> tuple[str, ...]:
    """Pitch names spelled by the key, each one iReal can write."""
    names = key_pitch_names(key)
    return tuple(name if _NAME.fullmatch(name) else FLAT_NAMES[pc] for pc, name in enumerate(names))


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("root", "bass", "tones", "uncertain", "no_chord")
    return all(a.get(key) == b.get(key) for key in keys)


def _symbol(reading: dict[str, Any], previous: dict[str, Any] | None, names: tuple[str, ...],
            losses: list[str]) -> str | None:
    """The iReal symbol of one reading, or None (a loss, written as a comment)."""
    if reading.get("no_chord"):
        return "n"
    if "voicing" in reading:
        losses.append("voicings")
    text = reading.get("text")
    stack = {key: value for key, value in reading.items() if key not in ("voicing", "weight")}
    if text is not None:
        try:
            if _same(read_symbol(text, previous), stack):
                return text
        except UnsupportedSymbol:
            pass
        if not any(key in reading for key in ("root", "bass", "tones")) and _TOKEN.match(text):
            return text   # the chart's own token, which grammar 1 left text-only
    if "root" not in reading:
        losses.append("text-only readings" if "bass" not in reading else "readings with no root")
        return None
    symbol = symbol_of({key: value for key, value in stack.items() if key != "text"}, names)
    if symbol is None and previous is not None and "tones" in previous:
        # iReal's invisible root: the chord before over a new bass.
        candidate = f"W/{names[reading['bass']]}"
        try:
            if _same(read_symbol(candidate, previous), stack):
                symbol = candidate
        except UnsupportedSymbol:
            pass
    if symbol is None:
        losses.append("uncertain tones and stacks no iReal quality writes")
    return symbol


def _comment(text: str | None) -> str:
    body = (text or "").replace("<", "").replace(">", "").strip()
    if not body or _STAFF.match(body):
        return ""
    return f"<{body}>"


# ---------------------------------------------------------------------------
# Bars


def _cells(fractions: list[Fraction], losses: list[str]) -> tuple[int, list[int]]:
    """The cell count of a bar and the cell of each chord, given each chord's
    offset as a fraction of the bar."""
    n = lcm(*(f.denominator for f in fractions)) if fractions else 1
    if 4 % n == 0:
        n = 4
    if n > MAX_CELLS:
        losses.append(f"chord offsets finer than {MAX_CELLS} cells a bar")
        n = MAX_CELLS
        cells = [min(MAX_CELLS - 1, round(f * n)) for f in fractions]
        for index in range(1, len(cells)):
            cells[index] = max(cells[index], cells[index - 1] + 1)
        if cells and cells[-1] >= MAX_CELLS:
            raise ExportRefused("more chords in one bar than iReal has cells")
        return n, cells
    return n, [int(f * n) for f in fractions]


def _write_cells(n: int, placed: dict[int, str]) -> str:
    out = []
    for index in range(n):
        token = placed.get(index, " ")
        if out and token != " " and out[-1] != " " and not token.startswith("<"):
            out.append(",")
        out.append(token)
    return "".join(out)


def render(request: dict[str, Any], records: list[dict[str, Any]]) -> tuple[bytes, list[str]]:
    """The link of one chart for this export request, and what was lost.
    Raises ExportRefused."""
    if request.get("format") != FORMAT:
        raise ExportRefused(f"this exporter writes the {FORMAT} format")
    if request.get("exporter") != EXPORTER:
        raise ExportRefused(f"this is exporter {EXPORTER['id']} version {EXPORTER['version']}")
    if len(request.get("rendered", [])) != 1 or request.get("joined"):
        raise ExportRefused("an iReal export renders exactly one lead sheet revision and joins nothing")
    style, tempo, transpose = _check_options(request.get("options", {}))
    found = _Records(records)
    lead = found.get(request["rendered"][0], "lead sheet")
    if lead.get("shape") != "claim" or _contract(lead) not in LEAD_SHEET:
        raise ExportRefused("an iReal export renders a lead sheet claim")
    if _role(lead, "source"):
        raise ExportRefused("a lead sheet timed on a recording cannot be an iReal chart: iReal has no time")
    losses: list[str] = []
    if _role(lead, "melody") or _role(lead, "lyrics") or _role(lead, "lyric_timing"):
        losses.append("melody, lyrics and underlays")

    if len(_role(lead, "work")) != 1:
        raise ExportRefused("a lead sheet pins exactly one Work")
    work = found.get(_role(lead, "work")[0], "work")
    title = (work.get("payload") or {}).get("label") or "Untitled"

    meters = [found.get(ref, "meter") for ref in _role(lead, "meter")]
    meters = [m for m in meters if _contract(m) in METERS and is_untimed_meter(_claim(m))]
    if not meters:
        raise ExportRefused("the lead sheet pins no untimed meter claim, so there are no bars to write")
    meter = meters[0]
    if len(meters) > 1:
        losses.append("every layer but the first written one")
    if not _role(meter, "sequence"):
        raise ExportRefused("the meter pins no chord sequence, so its bars hold no chords")
    sequence = found.get(_role(meter, "sequence")[0], "chord sequence")
    form = found.get(_role(meter, "form")[0], "form") if _role(meter, "form") else None
    try:
        check_chord_sequence_v2(_claim(sequence))
        if form is not None:
            check_form(_claim(form))
        # A version 2 payload is a version 3 payload (world/form.py).
        layers = check_meter_layers_v3(_claim(meter), form_claim=_claim(form) if form else None,
                                       sequence_claim=_claim(sequence))
    except ValueError as error:
        raise ExportRefused(f"an input breaks World's rules: {error}") from None
    if {ref["revision_id"] for ref in _role(lead, "chords")} - {sequence["revision_id"]}:
        losses.append("chord claims other than the one the meter indexes")
    written = [layer for layer in layers if layer.get("role") == "written"]
    layer = (written or layers)[0]
    if len(layers) > 1:
        losses.append("every layer but the first written one")
    if meter["payload"].get("modulations"):
        losses.append("groupings and modulations")
    if "weight_scale" in sequence["payload"]:
        losses.append("weights")

    key = None
    keys = [found.get(ref, "key") for ref in _role(lead, "key")]
    keys = [k for k in keys if _contract(k) == KEY_SEQUENCE]
    if len(_role(lead, "key")) > len(keys[:1]):
        losses.append("key regions and readings after the first")
    if keys:
        try:
            check_key_sequence(_claim(keys[0]))
        except ValueError as error:
            raise ExportRefused(f"the key breaks World's rules: {error}") from None
        regions = keys[0]["payload"]["regions"]
        if len(regions) > 1 or len(regions[0]["readings"]) > 1:
            losses.append("key regions and readings after the first")
        key = regions[0]["readings"][0]
    entries = sequence["payload"]["entries"]
    if transpose:
        key = transpose_key(key, transpose) if key is not None else None
        entries = [{"readings": [transpose_reading(r, transpose) for r in e["readings"]]} for e in entries]
    key_field = _key_field(key, losses)
    names = _names(key)

    style, tempo, first_is_style = _style_and_tempo(layer["bars"], style, tempo, losses)
    markup = _markup(layer, entries, form["payload"] if form else None, names, losses, first_is_style)
    # An empty composer field next to the empty unused field would write
    # `===`, iReal's song separator, so a chart with no credit names none.
    composer = _composer(found, lead, losses) or NO_COMPOSER
    fields = [_field(title, losses), _field(composer, losses), "", style, key_field, "",
              codec.music_field(markup), "", str(tempo), "0"]
    link = codec.encode_chart(codec.Song("irealb", tuple(fields), b""))
    codec.decode_playlist(link)   # what it wrote, it reads
    ordered = list(dict.fromkeys(losses))
    return link, ordered


def _style_and_tempo(bars: list[dict[str, Any]], style: str | None, tempo: int | None,
                     losses: list[str]) -> tuple[str, int, bool]:
    """The style and tempo fields: the options, else the first bar's first
    direction (ADR 0116, section 3), else Medium Swing and 0. Returns them and
    whether that direction is written as the fields (so not as a comment)."""
    directions = bars[0].get("directions", []) if bars else []
    first = directions[0] if directions else None
    if first is None:
        return (style or DEFAULT_STYLE), (tempo or 0), False
    written = None
    if "tempo" in first:
        bpm, beat = first["tempo"]["bpm"], first["tempo"].get("beat")
        if (bpm.isdigit() and 40 <= int(bpm) <= 300
                and (beat is None or beat == {"denominator": bars[0]["denominator"]})):
            written = int(bpm)
        elif tempo is None:
            losses.append("a direction's tempo iReal cannot write (fractional, outside 40 to 300, or "
                          "counted in another beat)")
    if tempo is None:
        tempo = written or 0
    if style is None:
        if "=" in first["text"]:
            losses.append("'=' in a style direction (iReal's field separator)")
            style = first["text"].replace("=", "-")
        else:
            style = first["text"]
    return style, tempo, True


def _field(text: str, losses: list[str]) -> str:
    if "=" in text:
        losses.append("'=' in the title or composer (iReal's field separator)")
        return text.replace("=", "-")
    return text


def _composer(found: _Records, lead: dict[str, Any], losses: list[str]) -> str:
    """The composer field: the composer credits' one verbatim `text` (the
    chart's own field, which ADR 0116 writes on every composer's credit), else
    their `credited_as` names. A contribution's role is `{"type": ...}`
    (ADR 0078); a credit in another role, or in none, has no iReal field."""
    texts, names = [], []
    for ref in _role(lead, "credits"):
        payload = found.get(ref, "credit").get("payload") or {}
        role = payload.get("role")
        if not isinstance(role, dict) or role.get("type") != "composer":
            losses.append("credits other than composers")
            continue
        if payload.get("text"):
            texts.append(payload["text"])
        if payload.get("credited_as"):
            names.append(payload["credited_as"])
    texts = list(dict.fromkeys(texts))
    if len(texts) == 1:
        return texts[0]          # one composer field, verbatim, as the chart had it
    return " ".join(dict.fromkeys(names))


def _key_field(key: dict[str, Any] | None, losses: list[str]) -> str:
    if key is None:
        return "C"
    text = key.get("text")
    if text and read_key(text) == {k: v for k, v in key.items() if k != "spelling"}:
        return text
    if key.get("mode") in ("major", "minor") and "tonic" in key:
        return _names(key)[key["tonic"]] + ("-" if key["mode"] == "minor" else "")
    losses.append("keys that are neither major nor natural minor")
    return "C"


def _markup(layer: dict[str, Any], entries: list[dict[str, Any]], form: dict[str, Any] | None,
            names: tuple[str, ...], losses: list[str], first_is_style: bool = False) -> str:
    bars = layer["bars"]
    count = len(bars)
    sections = form["sections"] if form else []
    repeats = form.get("repeats", []) if form else []
    jumps = form.get("jumps", []) if form else []
    first_bar: dict[int, int] = {}
    last_bar: dict[int, int] = {}
    for index, bar in enumerate(bars):
        if "section" in bar:
            first_bar.setdefault(bar["section"], index)
            last_bar[bar["section"]] = index
    if form and len(first_bar) != len(sections):
        losses.append("sections with no bars")

    opens = [""] * count     # "{" or "[" or ""
    closes = ["|"] * count
    before: list[list[str]] = [[] for _ in range(count)]   # tokens before a bar's cells
    texts: list[list[str]] = [[] for _ in range(count)]
    started: set[int] = set()     # bars the re-read starts a section at, without a mark
    after: set[int] = set()       # bars the re-read ends a section after
    ending_label: dict[int, str] = {}   # an ending's first section -> the label its ending gives
    ending_last: dict[int, int] = {}    # an ending's first section -> its last section

    for repeat in sorted(repeats, key=lambda r: r["first"]):
        inner = [r for r in repeats if r is not repeat and repeat["first"] <= r["first"] and r["last"] <= repeat["last"]]
        if inner:
            losses.append("nested repeats")
            continue
        if repeat["first"] not in first_bar or repeat["last"] not in last_bar:
            continue
        top = first_bar[repeat["first"]]
        opens[top] = "{"
        started.add(top)
        endings = repeat.get("endings", [])
        if not endings:
            end = last_bar[repeat["last"]]
            closes[end] = "}"
            after.add(end)
            if repeat["times"] != 2:
                texts[end].append(f"<{repeat['times']}x>")
            continue
        if len(endings) > 3:
            losses.append("more than three endings")
            endings = endings[:3]
        if [e["passes"] for e in endings] != [[n] for n in range(1, len(endings) + 1)] \
                or repeat["times"] != len(endings):
            losses.append("endings that are not one pass each")
        for number, ending in enumerate(endings, start=1):
            if ending["first"] not in first_bar or ending["last"] not in last_bar:
                continue
            start, end = first_bar[ending["first"]], last_bar[ending["last"]]
            before[start].append(f"N{number}")
            started.add(start)
            ending_label[ending["first"]] = f"{number}."
            ending_last[ending["first"]] = ending["last"]
            closes[end] = "}" if number < len(endings) else "]"
            after.add(end)

    if len(jumps) > 1:
        losses.append("more than one jump")
    for jump in jumps[:1]:
        if jump.get("repeats_on_return"):
            losses.append("jumps with repeats_on_return")
        end = last_bar.get(jump["after"])
        if end is None:
            continue
        what = {"da_capo": "D.C.", "dal_segno": "D.S."}[jump["kind"]]
        then = {"end": "", "fine": " al Fine", "coda": " al Coda"}[jump["then"]]
        texts[end].append(f"<{what}{then}>")
        after.add(end)
        if jump["kind"] == "dal_segno" and jump["to"] in first_bar:
            before[first_bar[jump["to"]]].append("S")
            started.add(first_bar[jump["to"]])
        if jump["then"] == "fine" and jump["fine"] in last_bar:
            texts[last_bar[jump["fine"]]].append("<Fine>")
            after.add(last_bar[jump["fine"]])
        if jump["then"] == "coda" and jump["coda_from"] in last_bar and jump["coda"] in first_bar:
            before[last_bar[jump["coda_from"]]].append("Q")
            after.add(last_bar[jump["coda_from"]])
            before[first_bar[jump["coda"]]].append("Q")
            started.add(first_bar[jump["coda"]])

    # Marks: wherever a section starts that the re-read would not start, or
    # whose label is not the mark in force.
    in_force = UNMARKED
    for section in sorted(first_bar):
        index = first_bar[section]
        label = sections[section]["label"]
        kind = sections[section].get("kind")
        caused = index == 0 or index in started or (index - 1) in after
        if section in ending_label:
            if label != ending_label[section] or kind is not None:
                losses.append("labels other than iReal's marks (written as comments)")
            # A mark inside an ending would end it on reading, so the mark the
            # ending's later sections continue is written where the ending starts.
            following = section + 1
            if following <= ending_last[section] and following in first_bar:
                wanted = sections[following]["label"]
                if wanted in MARKS and wanted != in_force:
                    before[index].insert(0, f"*{wanted}")
                    in_force = wanted
            continue
        if label in MARKS and MARKS[label] != kind:
            losses.append("section kinds other than intro and verse")
        if caused and label == in_force:
            continue
        if label in MARKS:
            before[index].insert(0, f"*{label}")
            if opens[index] == "":
                opens[index] = "["
            in_force = label
        else:
            losses.append("labels other than iReal's marks (written as comments)")
            comment = _comment(label)
            if comment:
                texts[index].insert(0, comment)

    # Directions after the style: comments on their bars, after any label.
    for index, bar in enumerate(bars):
        for number, direction in enumerate(bar.get("directions", [])):
            if index == 0 and number == 0 and first_is_style:
                continue
            if "tempo" in direction:
                losses.append("tempos of directions other than the first bar's first")
            comment = _comment(direction["text"])
            if comment[1:-1] != direction["text"]:
                losses.append("direction text iReal reads as a jump, Fine or count, or holding < or >")
            if comment:
                texts[index].append(comment)

    # Bars.
    out: list[str] = []
    time = None
    held = None
    effective_before = None
    previous_first = None
    for index, bar in enumerate(bars):
        if "grouping" in bar:
            losses.append("groupings and modulations")
        length = Fraction(bar["numerator"], bar["denominator"])
        signature = (bar["numerator"], bar["denominator"])
        head = opens[index]
        if signature != time:
            if signature in CODES:
                head += CODES[signature]
                time = signature
            else:
                losses.append("time signatures iReal has no code for")
                if time is None:
                    head += "T44"
                    time = (4, 4)
        head += "".join(before[index]) + "".join(texts[index])
        chords = [(Fraction(0) if "at" not in c else Fraction(c["at"]["count"], c["at"]["unit"]), c["entry"])
                  for c in bar.get("chords", [])]
        content = [(offset, entries[entry]["readings"]) for offset, entry in chords]
        effective = content if content else ([(Fraction(0), held)] if held is not None else [])
        as_x = None
        if effective_before:
            same = (len(effective_before) == 1 and effective_before[0][0] == 0
                    and effective_before[0][1] == held)
            as_x = [] if same else effective_before
        if index > 0 and as_x is not None and as_x == content and effective_before:
            body = " x  "
        else:
            placed: dict[int, str] = {}
            n, cells = _cells([offset / length for offset, _ in chords], losses)
            for (offset, entry), cell in zip(chords, cells):
                readings = entries[entry]["readings"]
                if len(readings) > 2:
                    losses.append("readings after the second")
                symbol = _symbol(readings[0], previous_first, names, losses)
                if symbol is None:
                    head += _comment(readings[0].get("text"))
                    continue
                else:
                    token = symbol
                    if len(readings) > 1:
                        alternate = _symbol(readings[1], readings[0], names, losses)
                        if alternate is not None and alternate != "n":
                            token += f"({alternate})"
                placed[cell] = token
                previous_first = readings[0]
            body = _write_cells(n, placed)
        if index > 0 and out[-1][-1] in "]}Z" and not opens[index] and body.startswith(" "):
            # After a closing line, spaces before a bar's first chord are layout
            # to iReal's reader; an opening `|` makes them cells.
            head = "|" + head
        close = closes[index]
        if index == count - 1 and close == "|":
            close = "Z"
        if close == "|" and index + 1 < count and opens[index + 1] == "[":
            close = "]"
        out.append(head + body + close)
        if content:
            held = content[-1][1]
            previous_first = content[-1][1][0]
        effective_before = effective
    return "".join(out)


# ---------------------------------------------------------------------------
# Playlists and the page iReal Pro opens


def playlist(links: list[bytes], name: str) -> bytes:
    """Several one-chart links as one playlist link, canonically encoded."""
    songs = tuple(codec.decode_playlist(link).songs[0] for link in links)
    return codec.encode_playlist(codec.Playlist("irealb", songs, name))


def page(link: bytes, title: str) -> bytes:
    """A small HTML page holding the link, which iReal Pro imports when the
    page is opened on a device that has it (as iReal's forum pages do)."""
    href = html.escape(link.decode("ascii"), quote=True)
    name = html.escape(title)
    return ("<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
            f"<title>{name}</title></head>\n<body><h3><a href=\"{href}\">{name}</a></h3>\n"
            "<p>Open this page on a device with iReal Pro and follow the link to import.</p>\n"
            "</body></html>\n").encode("utf-8")


def main(argv: list[str]) -> int:
    """`export.py <input.json> <output> <losses.json>`, as the relay runs an
    exporter."""
    if len(argv) != 3:
        print("usage: export.py <input.json> <output> <losses.json>", file=sys.stderr)
        return 2
    given = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    try:
        link, losses = render(given["request"], given["records"])
    except ExportRefused as error:
        print(f"lead-sheet-ireal: {error}", file=sys.stderr)
        return 1
    Path(argv[1]).write_bytes(link)
    Path(argv[2]).write_text(json.dumps(losses), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
