"""iReal Pro chart markup read into bars (ADR 0116, section 2).

`read_chart(markup)` takes a chart's unscrambled music field
(`codec.Song.music`) and returns its written bars: each bar's cells, the time
signature in force, the rehearsal mark, segno and coda, endings, staff text,
and how it opens and closes; and from those the chart's repeats and jumps.
It reads structure only. Chord symbols stay verbatim text here;
`symbols.read_symbol` reads them, and a symbol it cannot read is never a
reason for this module to fail.

The markup, as iReal Pro writes it:

| Markup | Meaning |
|---|---|
| `XyQ`, `LZ`, `Kcl` | spacing shorthands: three empty cells; an empty cell and `|`; `|`, an empty cell and `x` |
| `|` `[` `]` `{` `}` `Z` | bar lines: single, double open/close, repeat open/close, final |
| `T44` ... | a time signature (`TIME_SIGNATURES`) |
| `*A`..`*D`, `*i`, `*v` | rehearsal marks |
| `N1`..`N3` | endings |
| `S`, `Q`, `f` | segno, coda, fermata |
| `<text>` | staff text (jumps such as `D.C. al Coda`, `Fine`, a repeat count `3x`, or a comment) |
| a chord, `W/X`, `n`, `p`, `x`, `r`, a space | one cell each: a chord, an invisible root over a new bass, N.C., a slash, a one-bar repeat, a two-bar repeat, an empty cell |
| `(chord)` | an alternate chord: over the chord it follows, or alone in its own cell |
| `,` | separates chords without a cell |
| `s`, `l`, `Y`, `U` | small and large chords, vertical space, the end of playback: layout, kept nowhere but the document |

A chord is a root `A`..`G` with an optional `b` or `#`, a quality, and an
optional `/` and bass; its quality may hold a custom text between asterisks
(`C*text*`). What is a chord here is decided by shape, not by whether the
grammar knows the quality.

Structure this module cannot read raises `UnsupportedChart` with the reason:
an unknown token or time signature, a chart with no time signature before
its first chord, a repeat that does not open or close, nested repeats, an
ending outside a repeat or numbered out of order, `N0`, or a jump that the
form cannot state. A chart is never read in part.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

TIME_SIGNATURES: dict[str, tuple[int, int]] = {
    "T44": (4, 4), "T34": (3, 4), "T24": (2, 4), "T54": (5, 4), "T64": (6, 4), "T74": (7, 4),
    "T22": (2, 2), "T32": (3, 2), "T58": (5, 8), "T68": (6, 8), "T78": (7, 8), "T98": (9, 8),
    "T12": (12, 8),
}
MARKS = {"A": "A", "B": "B", "C": "C", "D": "D", "i": "i", "v": "v"}
ENDINGS = (1, 2, 3)

_QUALITY = r"(?:[-^+ho0-9b#]|sus|add|alt|\*[^*<>]*\*)*"
_NOTE = r"[A-G][b#]?"
_CHORD = rf"{_NOTE}{_QUALITY}(?:/{_NOTE})?"
_INVISIBLE = rf"W{_QUALITY}(?:/{_NOTE})?"
_TOKEN = re.compile(
    rf"(?P<text><[^>]*>)|(?P<time>T\d\d)|(?P<mark>\*[A-Za-z])|(?P<ending>N\d)"
    rf"|(?P<bar>[\[\]{{}}|Z])|(?P<chord>{_CHORD})(?P<attached>\({_CHORD}\)|\({_INVISIBLE}\))?"
    rf"|(?P<invisible>{_INVISIBLE})(?P<attached2>\({_CHORD}\))?"
    rf"|(?P<alone>\((?:{_CHORD}|{_INVISIBLE})\))"
    rf"|(?P<cell>[xrnp ])|(?P<flag>[SQf])|(?P<layout>[,slYU])"
)
_JUMP = re.compile(r"D\.([CS])\.(?: al (Coda|Fine))?\Z", re.IGNORECASE)
_FINE = re.compile(r"Fine\Z", re.IGNORECASE)
_TIMES = re.compile(r"(\d{1,2})x\Z")
_OFFSET = re.compile(r"\*\d\d")  # staff text's vertical offset, `<*72text>`


class UnsupportedChart(ValueError):
    """Structure the reader cannot read; the message names the reason."""


@dataclass(frozen=True)
class Cell:
    """One cell of a bar.

    `kind` is `chord`, `invisible` (W/X), `no_chord` (n), `slash` (p),
    `repeat_one` (x), `repeat_two` (r), `alternate` (a parenthesised chord
    alone in its cell), or `empty`. `text` is the markup verbatim (empty for an
    empty cell), and `alternate` the text inside an attached `(chord)`."""
    kind: str
    text: str = ""
    alternate: str | None = None
    small: bool = False
    fermata: bool = False


@dataclass
class Bar:
    number: int                      # 1-based, in written order
    time: tuple[int, int]            # the time signature in force
    opens: str = "|"                 # `|`, `[` or `{`
    closes: str = "|"                # `|`, `]`, `}` or `Z`
    cells: list[Cell] = field(default_factory=list)
    time_written: bool = False       # a time signature is written at this bar
    mark: str | None = None          # a rehearsal mark at this bar
    ending: int | None = None        # an ending begins at this bar
    segno: bool = False
    coda: bool = False
    end: bool = False                # `U`: iReal Pro's playback stops here
    texts: list[str] = field(default_factory=list)   # staff text, verbatim without its offset

    def chord_cells(self) -> list[tuple[int, Cell]]:
        """(cell index, cell) for every cell that is not empty."""
        return [(index, cell) for index, cell in enumerate(self.cells) if cell.kind != "empty"]

    def offset(self, index: int) -> Fraction:
        """ADR 0116's placement rule: cell k of n starts k/n of the bar after
        the downbeat. To be checked against iReal Pro's playback."""
        return Fraction(index, len(self.cells))


@dataclass(frozen=True)
class Ending:
    number: int
    first: int   # bar numbers, inclusive
    last: int


@dataclass(frozen=True)
class Repeat:
    first: int                 # the bar the repeat opens on
    last: int                  # the bar that closes it with `}` the last time
    endings: tuple[Ending, ...]
    times: int                 # how often the repeated bars are played
    times_text: str | None     # a `<Nx>` count written at the close, verbatim


@dataclass(frozen=True)
class Jump:
    kind: str                  # `da_capo` or `dal_segno`
    then: str | None           # `coda`, `fine`, or None (play to the end)
    at: int                    # the bar whose text states the jump
    text: str


@dataclass(frozen=True)
class Chart:
    bars: tuple[Bar, ...]
    repeats: tuple[Repeat, ...]
    jumps: tuple[Jump, ...]
    segno: int | None          # bar numbers
    coda_from: int | None      # the first `Q`: leave for the coda here
    coda: int | None           # the second `Q`: the coda begins here
    fine: int | None           # the bar carrying `<Fine>`
    comments: tuple[tuple[int, str], ...]   # staff text that is neither jump nor count, by bar


# ---------------------------------------------------------------------------
# Reading


def expand(markup: str) -> str:
    """Markup with the spacing shorthands written out. iReal Pro writes them
    inside staff text too (`<*20XyQFine>`), so they are expanded everywhere."""
    if markup.count("<") != markup.count(">"):
        raise UnsupportedChart("staff text that does not close")
    return markup.replace("XyQ", "   ").replace("LZ", " |").replace("Kcl", "| x")


def _tokens(markup: str) -> list[tuple[str, re.Match[str]]]:
    text = expand(markup).rstrip("\r\n ")
    out = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise UnsupportedChart(f"unknown token {text[position]!r} at {text[max(0, position - 8):position + 8]!r}")
        out.append((match.lastgroup if match.lastgroup not in ("attached", "attached2") else
                    ("chord" if match.group("chord") else "invisible"), match))
        position = match.end()
    return out


def read_chart(markup: str) -> Chart:
    """Read a chart's unscrambled markup. Raises UnsupportedChart."""
    bars: list[Bar] = []
    time: tuple[int, int] | None = None
    small = False
    pending: dict = {}           # marks met between bars, for the next bar
    current: Bar | None = None
    fermata = False              # a fermata waiting for its cell
    gap = False                  # after `]`, `}` or `Z` with no bar opened: spaces are not cells

    def bar() -> Bar:
        nonlocal current
        if current is None:
            if time is None and "time" not in pending:
                raise UnsupportedChart("no time signature before the first bar")
            current = Bar(number=len(bars) + 1, time=pending.pop("time", time), opens=pending.pop("opens", "|"))
            current.time_written = pending.pop("time_written", False)
            for name in ("mark", "ending", "segno", "coda"):
                if name in pending:
                    setattr(current, name, pending.pop(name))
            current.texts.extend(pending.pop("texts", []))
        return current

    for kind, match in _tokens(markup):
        token = match.group(0)
        if kind == "bar":
            if fermata and current is not None:
                _fermata_on_last(current)
            fermata = False
            gap = token in "]}Z"
            if current is not None and token in "[{":
                # An opening bar line ends the bar before it, as `|` would.
                _close(bars, current, "|")
                current = None
            if token in "[{":
                if "opens" in pending and pending["opens"] != "|":
                    raise UnsupportedChart(f"two opening bar lines in a row ({pending['opens']}{token})")
                pending["opens"] = token
            elif current is not None:
                _close(bars, current, token)
                current = None
            elif token in "]}Z" and bars and bars[-1].closes == "|" and "opens" not in pending:
                bars[-1].closes = token   # `|]`, `|}`: the closing line of the bar just ended
            elif token in "}" and "opens" in pending:
                raise UnsupportedChart("a repeat that closes on an empty bar")
            continue
        if kind == "time":
            if token not in TIME_SIGNATURES:
                raise UnsupportedChart(f"unknown time signature {token!r}")
            time = TIME_SIGNATURES[token]
            if current is not None and current.cells:
                raise UnsupportedChart(f"time signature {token!r} after the first cell of a bar")
            if current is None:
                pending["time"], pending["time_written"] = time, True
            else:
                current.time, current.time_written = time, True
            continue
        if kind == "mark":
            label = token[1]
            if label not in MARKS:
                raise UnsupportedChart(f"unknown rehearsal mark {token!r}")
            _set(current, pending, "mark", MARKS[label], token)
            continue
        if kind == "ending":
            number = int(token[1])
            if number not in ENDINGS:
                raise UnsupportedChart(f"ending {token!r}: only N1 to N3 are read")
            _set(current, pending, "ending", number, token)
            continue
        if kind == "text":
            body = _OFFSET.sub("", token[1:-1], count=1) if token[1:2] == "*" else token[1:-1]
            body = body.strip()
            if current is None:
                pending.setdefault("texts", []).append(body)
            else:
                current.texts.append(body)
            continue
        if kind == "flag":
            if token == "f":
                fermata = True   # over the next cell; `_close` keeps one with no cell after it
            else:
                _set(current, pending, "segno" if token == "S" else "coda", True, token)
            continue
        if kind == "layout":
            if token == "U":
                # Written before the bar line it ends at, or just after it.
                if current is not None:
                    current.end = True
                elif bars:
                    bars[-1].end = True
            elif token == "s":
                small = True
            elif token == "l":
                small = False
            continue
        if token == " " and current is None and gap and "opens" not in pending:
            continue
        target = bar()
        start = len(target.cells)
        if kind == "cell":
            target.cells.append(Cell({"x": "repeat_one", "r": "repeat_two", "n": "no_chord", "p": "slash",
                                      " ": "empty"}[token], "" if token == " " else token, small=small))
        elif kind == "alone":
            target.cells.append(Cell("alternate", token[1:-1], small=small))
        else:
            head = match.group("chord") or match.group("invisible")
            attached = match.group("attached") or match.group("attached2")
            target.cells.append(Cell(kind, head, attached[1:-1] if attached else None, small=small))
        if fermata and target.cells[start].kind != "empty":
            target.cells[start] = _replace(target.cells[start], fermata=True)
            fermata = False
    if current is not None:
        if fermata:
            _fermata_on_last(current)
        if any(cell.kind != "empty" for cell in current.cells) or current.texts:
            _close(bars, current, "|")
    if bars and "texts" in pending:
        # Staff text after the final bar line is written over the last bar.
        bars[-1].texts.extend(pending.pop("texts"))
    leftover = {key for key in pending if key not in ("opens", "time", "time_written")}
    if leftover:
        raise UnsupportedChart(f"{', '.join(sorted(leftover))} after the last bar")
    if not bars:
        raise UnsupportedChart("no bars")
    return _structure(bars)


def _fermata_on_last(bar: Bar) -> None:
    """A fermata with no chord after it in its bar: over the bar's last chord."""
    chosen = bar.chord_cells()
    if not chosen:
        raise UnsupportedChart(f"a fermata over a bar with no chord (bar {bar.number})")
    index, cell = chosen[-1]
    bar.cells[index] = _replace(cell, fermata=True)


def _replace(cell: Cell, **changes) -> Cell:
    values = {name: getattr(cell, name) for name in ("kind", "text", "alternate", "small", "fermata")}
    values.update(changes)
    return Cell(**values)


def _set(current: Bar | None, pending: dict, name: str, value, token: str) -> None:
    if current is not None and not current.cells:
        target_value = getattr(current, name)
        if target_value not in (None, False):
            raise UnsupportedChart(f"two {name}s on one bar ({token!r})")
        setattr(current, name, value)
        return
    if current is not None:
        # A mark after the bar's first cell: iReal Pro draws it at the bar.
        if getattr(current, name) not in (None, False):
            raise UnsupportedChart(f"two {name}s on one bar ({token!r})")
        setattr(current, name, value)
        return
    if name in pending:
        raise UnsupportedChart(f"two {name}s on one bar ({token!r})")
    pending[name] = value


def _close(bars: list[Bar], bar: Bar, line: str) -> None:
    if not bar.cells:
        # Bar lines around nothing but marks: a bar with no cells is not
        # written, and its marks would be lost.
        if bar.mark or bar.ending or bar.segno or bar.coda or bar.texts:
            raise UnsupportedChart(f"marks on a bar with no cells (bar {bar.number})")
        return
    bar.closes = line
    bars.append(bar)


# ---------------------------------------------------------------------------
# Repeats, endings and jumps


def _structure(bars: list[Bar]) -> Chart:
    jumps, segno, coda_from, coda, fine, comments = _jumps(bars)
    repeats: list[Repeat] = []
    open_at: int | None = None                  # a `{` whose `}` is still to come
    first = 0                                   # the bar the repeat being read opened on
    endings: list[Ending] = []
    running: tuple[int, int] | None = None      # (number, first bar) of the ending being read
    awaiting: int | None = None                 # the bar a `}` closed an ending on; the next ending is due
    count: str | None = None                    # a `<Nx>` written at a closing bar

    def done() -> None:
        nonlocal endings, count, awaiting
        repeats.append(Repeat(first, closed_last, tuple(endings), endings[-1].number, count))
        endings, count, awaiting = [], None, None

    closed_last = 0
    for bar in bars:
        n = bar.number
        if running is not None and open_at is None and (bar.opens in "[{" or bar.mark or bar.ending):
            endings.append(Ending(running[0], running[1], n - 1))
            running = None
            done()
        if awaiting is not None and bar.ending is None:
            raise UnsupportedChart(f"the repeat closing at bar {awaiting} has no ending after it")
        if bar.ending is not None:
            due = endings[-1].number + 1 if endings else 1
            if running is not None:
                raise UnsupportedChart(f"ending N{bar.ending} inside ending N{running[0]} (bar {n})")
            if bar.ending != due:
                raise UnsupportedChart(f"ending N{bar.ending} where N{due} is due (bar {n})")
            if (due == 1 and open_at is None) or (due > 1 and awaiting is None):
                raise UnsupportedChart(f"ending N{bar.ending} outside a repeat (bar {n})")
            running, awaiting = (bar.ending, n), None
        if bar.opens == "{":
            if open_at is not None:
                raise UnsupportedChart(f"a repeat opens inside another (bar {n})")
            if running is not None:
                raise UnsupportedChart(f"a repeat opens inside an ending (bar {n})")
            open_at = first = n
        counts = [text.strip() for text in bar.texts if _TIMES.match(text.strip())]
        if bar.closes == "}":
            if open_at is None and running is None:
                raise UnsupportedChart(f"a repeat closes that never opened (bar {n})")
            if counts:
                count = counts[-1]
            closed_last = n
            if running is not None:
                endings.append(Ending(running[0], running[1], n))
                running, awaiting, open_at = None, n, None
            else:
                repeats.append(Repeat(first, n, (), _times(count), count))
                open_at, count = None, None
        elif bar.closes in "]Z" and running is not None and open_at is None:
            endings.append(Ending(running[0], running[1], n))
            running = None
            done()
        elif counts:
            raise UnsupportedChart(f"a repeat count {counts[-1]!r} away from a closing repeat (bar {n})")
    if open_at is not None:
        raise UnsupportedChart(f"a repeat opened at bar {open_at} never closes")
    if awaiting is not None:
        raise UnsupportedChart(f"the repeat closing at bar {awaiting} has no ending after it")
    if running is not None:
        endings.append(Ending(running[0], running[1], bars[-1].number))
        done()

    return Chart(tuple(bars), tuple(repeats), jumps, segno, coda_from, coda, fine, comments)


def _jumps(bars: list[Bar]) -> tuple[tuple[Jump, ...], int | None, int | None, int | None, int | None,
                                     tuple[tuple[int, str], ...]]:
    """Segno, coda signs, Fine and the jumps in staff text; the rest of the
    staff text, other than repeat counts, as comments."""
    segnos = [bar.number for bar in bars if bar.segno]
    codas = [bar.number for bar in bars if bar.coda]
    if len(segnos) > 1:
        raise UnsupportedChart(f"{len(segnos)} segnos")
    if len(codas) > 2:
        raise UnsupportedChart(f"{len(codas)} coda signs")
    jumps: list[Jump] = []
    comments: list[tuple[int, str]] = []
    fine: int | None = None
    for bar in bars:
        for text in bar.texts:
            stripped = text.strip()
            jump = _JUMP.match(stripped)
            if jump:
                kind = "da_capo" if jump.group(1).upper() == "C" else "dal_segno"
                then = jump.group(2).lower() if jump.group(2) else None
                jumps.append(Jump(kind, then, bar.number, text))
            elif _FINE.match(stripped):
                if fine is not None:
                    raise UnsupportedChart("two Fines")
                fine = bar.number
            elif re.match(r"D\.[CS]\.", stripped):
                raise UnsupportedChart(f"a jump the form cannot state: {stripped!r}")
            elif not _TIMES.match(stripped):
                comments.append((bar.number, text))
    if len(jumps) > 1:
        raise UnsupportedChart(f"{len(jumps)} jumps")
    for jump in jumps:
        if jump.kind == "dal_segno" and not segnos:
            raise UnsupportedChart(f"{jump.text!r} with no segno")
        if jump.then == "coda" and len(codas) != 2:
            raise UnsupportedChart(f"{jump.text!r} needs two coda signs, the chart has {len(codas)}")
        if jump.then == "fine" and fine is None:
            raise UnsupportedChart(f"{jump.text!r} with no Fine")
    if len(codas) == 1 or (len(codas) == 2 and not any(jump.then == "coda" for jump in jumps)):
        raise UnsupportedChart(f"{len(codas)} coda sign(s) with no jump to a coda")
    return (tuple(jumps), segnos[0] if segnos else None, codas[0] if len(codas) == 2 else None,
            codas[1] if len(codas) == 2 else None, fine, tuple(comments))


def _times(text: str | None) -> int:
    if text is None:
        return 2
    count = int(_TIMES.match(text).group(1))
    if count < 2:
        raise UnsupportedChart(f"a repeat played {text!r}")
    return count
