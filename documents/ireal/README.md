# iReal Pro

The iReal Pro importer ([ADR 0116](../../docs/decisions/0116-ireal-charts.md),
proposed), standard library only. The codec, chart reader, grammar and claims
are pure; `batches.py`, `importer.py` and `links.py` write Door proposals and
run `door admit`. Tests use invented charts only (suites `documents` and
`documents-door`).

| File | What it is |
|---|---|
| [`codec.py`](codec.py) | `decode_playlist` (an `irealb://` or `irealbook://` link into songs, each with its verbatim slice), `chart_link`, `unscramble`/`scramble`, `encode_chart`/`encode_playlist` (one canonical encoding, stable under decode and encode) |
| [`chart.py`](chart.py) | `read_chart`: a song's markup into bars, cells, time signatures, marks, repeats with endings, segno, coda, Fine and jumps; `UnsupportedChart` for structure it cannot read |
| [`symbols.py`](symbols.py) | grammar version 1: `read_symbol` (a chord into a chord-stack reading, `UnsupportedSymbol` naming what it cannot read), `reading_of` (a text-only reading instead), `symbol_of` (a reading back to a symbol), `read_key` |
| [`claims.py`](claims.py) | `chart_claims`: a chart into `chord_sequence` v2, `form` v1, an untimed `meter_layers` v2 written layer and `key_sequence` v1; a chart World refuses raises `UnsupportedChart`, so none is read in part |
| [`batches.py`](batches.py) | proposal directories with revisions planned ahead; `combined` joins a playlist's batches for one dry run |
| [`importer.py`](importer.py) | `run_import`: the playlist and chart documents, their source claims, one `ireal-chart` run per chart and its claims; a rerun on unchanged input writes nothing |
| [`links.py`](links.py) | `candidates` (Work and composer candidates with evidence, never by title alone) and `run_link` (the operator's confirmed links, composer contributions, `lead_sheet`) |
| `test_*.py` | one per pure module |

Links in the wild are percent-encoded by different programs (some escape
every byte, some escape `=` and the song separator), so a chart's slice of
its playlist is its evidence, and its canonical encoding is a view.
