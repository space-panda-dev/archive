# Documents

Importers of outside documents: files a person brings that hold music
written down, read into claims about the document
([ADR 0116](../docs/decisions/0116-ireal-charts.md), proposed). One directory
per document format; standard library only.

| Directory | What it is |
|---|---|
| [`ireal/`](ireal/README.md) | iReal Pro links: the codec, the chart reader and the chord grammar |

Nothing here admits anything yet: the importer, the claims and the commands
wait for ADR 0116's acceptance, and [boundaries](../docs/boundaries.md) gains
this directory then.
