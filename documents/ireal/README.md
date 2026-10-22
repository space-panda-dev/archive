# iReal Pro

The pure core of the iReal Pro importer and its exporter ([ADR 0116](../../docs/decisions/0116-ireal-charts.md),
proposed): no archive, no Door, standard library only. Tests use invented
charts only (suite `documents`).

| File | What it is |
|---|---|
| [`codec.py`](codec.py) | `decode_playlist` (an `irealb://` or `irealbook://` link into songs, each with its verbatim slice), `chart_link`, `unscramble`/`scramble`, `encode_chart`/`encode_playlist` (one canonical encoding, stable under decode and encode) |
| [`chart.py`](chart.py) | `read_chart`: a song's markup into bars, cells, time signatures, marks, repeats with endings, segno, coda, Fine and jumps; `UnsupportedChart` for structure it cannot read |
| [`symbols.py`](symbols.py) | grammar version 1: `read_symbol` (a chord into a chord-stack reading, `UnsupportedSymbol` naming what it cannot read), `reading_of` (a text-only reading instead), `symbol_of` (a reading back to a symbol), `read_key` |
| [`export.py`](export.py) | exporter `lead-sheet-ireal` version 1: an untimed lead sheet as an `irealb://` link of one chart, with its losses; `playlist` (links into one playlist) and `page` (the HTML iReal Pro imports from); runs as the relay runs an exporter |
| [`stand_in.py`](stand_in.py) | a stand-in for the importer's reading of a chart into claim payloads, naming each choice it makes, so the exporter's round trip is tested; the importer's claim builder replaces it |
| `test_*.py` | one per module; `test_export` reads, exports and reads again |

Links in the wild are percent-encoded by different programs (some escape
every byte, some escape `=` and the song separator), so a chart's slice of
its playlist is its evidence, and its canonical encoding is a view.
