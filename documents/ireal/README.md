# iReal Pro

The iReal Pro importer ([ADR 0116](../../docs/decisions/0116-ireal-charts.md),
accepted), standard library only. The codec, chart reader, grammar, claims and
exporter are pure; `batches.py`, `importer.py` and `links.py` write Door proposals and
run `door admit`. Tests use invented charts only (suites `documents` and
`documents-door`).

| File | What it is |
|---|---|
| [`codec.py`](codec.py) | `decode_playlist` (an `irealb://` or `irealbook://` link into songs, each with its verbatim slice), `chart_link`, `unscramble`/`scramble`, `encode_chart`/`encode_playlist` (one canonical encoding, stable under decode and encode) |
| [`chart.py`](chart.py) | `read_chart`: a song's markup into bars, cells, time signatures, marks, repeats with endings, segno, coda, Fine and jumps; `UnsupportedChart` for structure it cannot read |
| [`symbols.py`](symbols.py) | grammar version 1: `read_symbol` (a chord into a chord-stack reading, `UnsupportedSymbol` naming what it cannot read), `reading_of` (a text-only reading instead), `symbol_of` (a reading back to a symbol), `read_key` |
| [`claims.py`](claims.py) | `chart_claims`: a chart into `chord_sequence` v2, `form` v1, an untimed `meter_layers` v3 written layer (the style, tempo and comments as its directions) and `key_sequence` v1; a chart World refuses raises `UnsupportedChart`, so none is read in part |
| [`batches.py`](batches.py) | proposal directories with revisions planned ahead; `combined` joins batches for a dry run, which predicts them in groups under Door's proposal size limit |
| [`importer.py`](importer.py) | `run_import`: the playlist and chart documents, their source claims, one `ireal-chart` run per chart and its claims; a rerun on unchanged input writes nothing |
| [`links.py`](links.py) | `candidates` (Work and composer candidates with their evidence; a title alone is never a link) and `run_link` (the operator's confirmed links, composer contributions, `lead_sheet` v2) |
| [`export.py`](export.py) | exporter `lead-sheet-ireal` version 1: an untimed lead sheet as an `irealb://` link of one chart, with its losses; `playlist` (links into one playlist) and `page` (the HTML iReal Pro imports from); runs as the relay runs an exporter |
| [`stand_in.py`](stand_in.py) | World's records of a chart as the importer's own reader (`claims.chart_claims`) reads it, so the exporter's round trip tests the importer and the exporter together |
| `test_*.py` | one per module; `test_export` reads, exports and reads again |

Links in the wild are percent-encoded by different programs (some escape
every byte, some escape `=` and the song separator), so a chart's slice of
its playlist is its evidence, and its canonical encoding is a view.
