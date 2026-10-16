# World v1 prototype

World builds a disposable RDF view from one complete `archive export` result.
The export pins a Git commit and carries the committed context, every
installed contract bundle, and raw record revisions. World does not read archive worktree files
or write to the archive. Its store is regenerable; `CURRENT` selects an
immutable generation built from one snapshot and projection version.

## Install

Use a virtual environment and the dependency pinned to the initial Oxigraph
minor release:

```sh
python3 -m venv .venv-world
. .venv-world/bin/activate
python -m pip install -r world/requirements.txt
```

## Rebuild and query

The archive command must be available on `PATH`:

```sh
world/world.py rebuild /path/to/archive --store /path/to/world-store
world/world.py query --store /path/to/world-store --query-file question.sparql [--projection N]
```

A query answers in the W3C formats: SPARQL 1.1 Query Results JSON for SELECT
and ASK; for CONSTRUCT and DESCRIBE, N-Triples by default, or Turtle or
JSON-LD with `--rdf-format`. World's data is in named
graphs; the default graph is empty, so name the graphs (`GRAPH ?g { … }`). A
query using `SERVICE` is refused: World answers only from its own store.

### Patterns that use the indexes

`{archive}` is the archive's identifier, which the relay's `/health` answers.
Every claim and assessment revision has its own graph, named by the revision,
and its node in the records graph has its shape as `rdf:type`. Reach claim
graphs through that node; a filter on graph names reads every quad in the
store first.

```sparql
PREFIX a: <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#>
SELECT ?g ?s ?p ?o WHERE {
  GRAPH <{archive}#world:records> { VALUES ?t { a:Claim a:Assessment } ?g a ?t }
  GRAPH ?g { ?s ?p ?o } }
```

Each record links to the contracts it cites with `dcterms:conformsTo`. Select
by contract through that link, not by matching the `a:contracts` JSON text:

```sparql
PREFIX dcterms: <http://purl.org/dc/terms/>
SELECT ?g ?s ?p ?o WHERE {
  GRAPH <{archive}#world:context> { ?contract dcterms:identifier "archive.assessment/judgment" }
  GRAPH <{archive}#world:records> { ?g dcterms:conformsTo ?contract }
  GRAPH ?g { ?s ?p ?o } }
```

Add `?contract a:version "1"` to the first graph pattern to pin a version. To
leave a contract out, use `FILTER NOT EXISTS` around the link. Both patterns
are checked against the text matches they replace in World's tests.

To use another `archive` executable, pass `--archive-command /path/to/archive`.
For a captured export, pass `--export-file export.json`; the positional archive
root is still required so World can enforce that the store lives outside it.
`world build export.json --output projection.nq` writes deterministic N-Quads
for inspection without opening a persistent store.

## Refresh

```sh
world/world.py refresh /path/to/archive --store /path/to/world-store
```

`refresh` extends the selected generation when it was built by the same
projection version for the same archive, under the same core context, from an
ancestor of HEAD: it asks `archive export --since <snapshot>` for the records
added since, copies the generation (a RocksDB checkpoint, mostly hard links),
projects only those records into the copy, and selects it
([ADR 0055](../docs/decisions/0055-incremental-world-refresh.md)). In any other
case it rebuilds in full. It reports `incremental`, `full` (with the reason),
or `unchanged`; a failure leaves the previous generation selected. Deleting
the store is always safe.

The records, context, and domain graphs are archive-wide
(`<archive>#world:records`, `#world:context`, `#world:domain`); each
generation's metadata records the snapshot it projects. Each immutable revision
remains a PROV entity; claims and assessments also remain Web Annotation
annotations. Archive's local JSON-LD context and the record's cited contract
contexts supply the field and reference terms. Claims, exact reference pins,
writer, asserting agent, timestamp, and payload remain queryable. Audio
selection pieces expose their ordered sample bounds and exact Artifact pins.
Song occurrences point to exact selection Claim revisions, and version 2
performance-to-Work claims point from an exact occurrence revision to an exact
Work revision. These domain links are scoped to each asserting Claim graph.
Version 1 performance-to-Work claims remain structural assertions; they do
not establish a song occurrence. Song occurrence version 2 and
`performance_work` version 3 project like their predecessors, for
multi-source selections. Specification 0.0.5 selections expose each
source track and its spans; capture alignments expose their anchors; titles,
kinds (as SKOS concepts), ordered artist credits, and session parts stay in
the graph of the claim that asserted them; and a judgment names the exact
claim revision it judged, with its verdict. Competing titles or kinds appear
side by side. Migration translation records remain in the records graph with
their pins. World does not answer `current` yet.

## What World emits

[`projection/6/`](projection/6/projection.json) declares what projection
version 6 emits: its named graphs, a closed SHACL shapes file for each, and
the Archive vocabulary they use. `world/conformance.py --store <store>` (or
`--export <file>`) checks a projection against it; it needs
`world/requirements-test.txt`. Additions keep a projection's version; renames
and removals make a new version, served beside the previous one for at least
90 days. A store keeps `CURRENT-p<N>` for each served version and `CURRENT` for
the newest; `refresh` and `rebuild` build every served version
([ADR 0059](../docs/decisions/0059-world-read-contract.md)). The context graph
states the projection's version and the archive's specification:
`<{archive}#world> arch:projection_version 6`.

## Unsupported behavior

`world current` and `world promote` return `status: unsupported`. No per-kind
current rule exists, so World presents all supported performance-to-work and
work-to-MusicBrainz claims, including competing mappings, with their exact
claim revisions and attribution. It does not select a winner. See the accepted
[work-correspondence decision](../docs/decisions/0042-work-correspondence.md)
and the SPARQL examples in `world/examples/`.

`world musicbrainz-work <MBID>` performs one targeted lookup of a MusicBrainz
Work. A successful result is labeled `candidate`; network, not-found, and bad
response results are labeled `unavailable`. The result is disposable and is
never admitted to Archive or promoted automatically. A person's judgment can
support a correspondence claim; any fetched response used as evidence must
first be admitted and pinned separately.

An incomplete or unresolved export fails with `status: incomplete`; an
unsupported shape, context term, or requested feature fails with
`status: unsupported`. Neither result is an empty graph or a successful
zero-match answer.

## Tests

```sh
python -m pip install -r world/requirements-test.txt
ARCHIVE_TEST_DOOR=$(cabal list-bin door) ARCHIVE_TEST_ARCHIVE=$(cabal list-bin archive) \
  python -m unittest discover -s world/tests -v
```

The conformance tests build the [fixture archive](../fixture/README.md)
through the real Door, so they need the two variables; without them they are
skipped, unless `ARCHIVE_REQUIRE_INTEGRATION` is set, when they fail. They
require the fixture to cite every contract in the specification's catalog
except the six that specification 0.0.7 adds, which World does not project
yet, every served projection of it to conform, and every declared term to appear in
it.

The bootstrap export fixture was captured from a synthetic archive created by
`archive init` and emitted by `archive export`. The Door chain fixture is the
export of the archive built by Door's World admission test; regenerate it by
running `cabal test door-test` with `ARCHIVE_TEST_WORLD_EXPORT` set to its
path. Every example query must return its expected rows against that fixture.
The same run captures the export at a cut before the review records
(`ARCHIVE_TEST_WORLD_CUT_EXPORT`, `door-world-chain-cut-export.json`) and the
export since that cut (`ARCHIVE_TEST_WORLD_SINCE_EXPORT`,
`door-world-chain-since-export.json`); regenerate all three together. The
refresh tests require an incremental generation built from them to equal a
full rebuild, quad for quad and row for row on every example query.
The remaining fixtures exercise
claims, assessments, audio selections and occurrences, performance links,
competing MusicBrainz correspondences,
conflicting contract contexts, deterministic projection, and unresolved
references. MusicBrainz lookup behavior is tested without making network calls.
