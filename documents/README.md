# Documents

Importers of outside documents: files a person brings that hold music
written down, read into claims about the document
([ADR 0116](../docs/decisions/0116-ireal-charts.md), proposed). One directory
per document format; standard library only.

| Directory | What it is |
|---|---|
| [`ireal/`](ireal/README.md) | iReal Pro links: the codec, the chart reader, the chord grammar, the importer and Work links |

`documents.py` is the command line: `ireal import FILE --archive A --staging S
[--dry-run]` proposes a playlist's documents and claims to Door;
`ireal candidates FILES --archive A --out DIR` writes a private candidates
file and a decisions template (never inside the repository or the archive);
`ireal link DECISIONS --archive A` asserts what the operator confirmed.
`tests/` runs them through the real Door (suite `documents-door`).
ADR 0116 is accepted; [boundaries](../docs/boundaries.md) lists this
directory and its command.
