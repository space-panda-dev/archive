# 0059. World publishes a versioned read contract

Status: accepted

World is getting its first readers outside this repository, and the
repository is to be published. A reader's only way to learn what World
answers was to read `world/world.py`:

- query answers were each term's N-Triples text (`str(term)`), so a client
  parsed `"5"^^<…#integer>` itself, and a zero-row answer lost its variable
  names. No decision chose that format;
- nothing said which graphs, classes, or predicates World emits. Some
  predicates (`arch:performance_kind`, `arch:credit`) appear in no contract,
  and a renamed predicate makes a client's query return nothing, which looks
  like an empty archive;
- the projection version appeared only in store directory names;
- World listed only the contracts some record cites, so a client could not
  confirm a contract it was about to use for the first time;
- any SPARQL `SERVICE` clause made the archive machine fetch the URL it
  named.

The operator decided on 2026-10-15 that the boundary stays strict: World's
promises are made to any reader, and a stranger must be able to build a
client from what Archive publishes and learn from their own CI that a release
broke it.

## Decision

**Answers use the W3C formats.** A SELECT or ASK answer is a SPARQL 1.1 Query
Results JSON document, with typed terms and `head.vars` even when there are no
rows. A CONSTRUCT or DESCRIBE answer is N-Triples unless the caller asks for
Turtle or JSON-LD (expanded, as a JSON value); N-Triples is the default because
any RDF library reads it a line at a time. A new graph format is an addition
and keeps the projection version. World checks its SELECT
encoding against the engine's own serializer in its tests. A bound on the
number of rows refuses an answer that would exceed it; it never truncates.

**Answers come only from World's store.** A query containing the `SERVICE`
keyword outside a string, IRI, or comment is refused before the engine runs.
This narrows ADR 0041's read-only SPARQL to what it promised: metadata
projected from records.

**Each projection version is described.** `world/projection/<N>/` holds:

- `projection.json`: the named graphs version N emits, as IRI patterns, each
  with a SHACL shapes file. World's default graph is empty;
- one SHACL shapes file per graph kind (records, context, domain, and one
  graph per claim or assessment revision). Every shape is closed;
- `vocabulary.ttl`: every Archive term those shapes use, with its meaning.

SHACL is the W3C language for describing RDF graphs, readable by any SHACL
engine. A projection conforms when its default graph is empty, every named
graph matches exactly one pattern and validates against that pattern's
shapes, and every subject is a focus node of some closed shape. Together these
leave no graph, class, or predicate undeclared. `world/conformance.py` checks
a store or an export.

**Archive's tests check the real fixture graph.** `fixture/plan.json` is a
published, synthetic set of records that cites every contract in the current
specification's catalog (ADR 0057). `fixture/build.py` creates a fresh archive
with `archive init` and admits each step through the real `door admit` or
`door transition`, including content through a staging git-annex remote. It
never writes a record itself; it only checks that Door committed the
revisions it pinned. World's tests build that archive, check that it cites the
whole catalog, project it, and require it to conform. They also require every
declared predicate and class to appear in it, except three destroy roles that
Door refuses until their evidence contracts exist. A declaration that names
something World never emits fails as surely as one that misses something
World does emit.

**The projection states its version.** The context graph holds
`<{archive}#world> a arch:WorldProjection`, with `arch:projection_version`,
`dcterms:conformsTo arch:world-projection-<N>`, and `arch:spec` (the
archive's specification). Every relay answer names the served projection
versions and the specification too (ADR 0060).

**World lists every installed contract.** `archive export` now carries every
contract bundle the archive holds, cited or not, each verified against its
digest path; `archive export --since` still carries only the bundles its
records cite. World projects each bundle into the context graph. This
replaces ADR 0041's "exact cited contracts" for the full export.

**Versioning.** Adding a graph, class, predicate, or optional value to a
projection keeps its version, and its shapes and vocabulary grow in the same
change. Renaming or removing any of them, changing a datatype, IRI pattern,
or ordering convention, or changing what an existing predicate means bumps the
version.

**Overlap window.** A new projection version is served beside the previous
one for at least 90 days after the release that introduces it. World builds
both from each refresh; `world/world.py` keeps the previous projector until
the window closes. The store keeps one pointer per served version,
`CURRENT-p<N>`, as well as `CURRENT` for the newest. A query names the
version it was written for, so a new version never changes an old client's
answers; asking for a version that is not served is refused, never answered
from another. When the window closes, World stops serving that version and
removes its pointer. The release notes give the date it closes.

## Consequences

- A client can be generated and checked from published files alone: the
  shapes, the vocabulary, the relay's OpenAPI description (ADR 0060), and the
  fixture archive (ADR 0062).
- `world query` now prints SPARQL Results JSON or a graph format, and takes
  `--projection` and `--rdf-format`. `world refresh` refreshes every served version and reports
  each.
- Projection 6 gained the projection description and the uncited contract
  bundles. Both are additions, so the version stays 6, and an existing
  generation gains them on its next refresh.
- Revision 2 of projection 6 links each record revision to the contracts it
  cites: `dcterms:conformsTo` the bundle's node in the context graph,
  `<{archive}#world:contract:{id}:{version}>`, beside the `arch:contracts`
  JSON it repeats. It is an addition, so the version stays 6 and no window
  opens. But an incremental refresh projects only new records, so each
  addition raises the version's revision, recorded in the generation's
  metadata and name (`{snapshot}-p6r2`), and a generation built at an earlier
  revision is rebuilt in full. World's README gives the query patterns the
  links allow: select claim graphs through the records graph's index rather
  than by scanning graph names, and select by contract through the link
  rather than by matching JSON text. At shadow size the first takes 32 ms
  where the scan takes 213 ms.
- The first use of the overlap window will be the first rename.

## Alternatives considered

- **Keep the text terms and document them.** Every client would parse RDF term
  syntax, and an empty answer would still drop its variables.
- **ShEx instead of SHACL.** Comparable, but less widely implemented, and not
  a W3C recommendation.
- **An OWL ontology alone.** It describes terms, not which graph holds what,
  and it is open-world: it cannot say that nothing else appears.
- **Generate the shapes from World's output.** The declaration would agree
  with whatever World does, including a mistake.
- **A default version when the query names none.** A new version would then
  change old clients' answers silently.
- **Hand-written fixture exports.** They agree with equally wrong code; the
  fixture archive is written by Door.
