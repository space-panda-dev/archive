# 0055. World refreshes incrementally

Status: accepted

World rebuilds its whole store from a full `archive export` on every refresh
(ADR 0041). Every graph name includes the snapshot commit, so no part of one
generation can be reused for the next. On the first semantic shadow a full
export, projection, and query report took about 10 seconds at a few thousand
records.
The review application will refresh World after each write, and the archive
will keep growing.

Records are immutable, and Door only ever adds them. A record's projection
depends only on the record, the earlier records it pins, and the contracts and
context it cites. Adding records never changes an earlier record's quads.

## Decision

**Graph names no longer include the snapshot.** Projection version 6 names the
archive-wide records, context, and domain graphs `<archive>#world:records`,
`#world:context`, and `#world:domain`. Each generation's metadata records the
snapshot it projects, as it does now. Claim and assessment graphs keep the
names of their exact revisions.

**`archive export --since <commit>` exports what was added.** It reads one
committed HEAD as `archive export` does, and emits only the records added
after `<commit>`, together with every record they pin and the contracts those
records cite. It refuses when `<commit>` is not an ancestor of HEAD. The
records it adds are marked separately from the pinned ones.

**A refresh extends the current generation.** When the current generation was
built by the same projection version from an ancestor of HEAD, World copies
it, projects the added records into the copy, and selects the copy as a new
generation. In any other case World rebuilds in full. Selection stays atomic
(ADR 0041), and a failed refresh leaves the previous generation selected.

**An incremental generation equals a full one.** Tests compare the quads of an
incrementally refreshed generation with a full rebuild of the same snapshot,
for every example query and for the whole dataset.

## Consequences

- A refresh costs work proportional to the records added since the last one.
- The store directory can be deleted at any time; the next refresh rebuilds
  it in full.
- Queries that bind graphs by variable, as every example does, are unaffected.

## Alternatives considered

- **Keep snapshot-scoped graph names and rename graphs in place.** Renaming
  touches every quad, which is the cost this record removes.
- **Query the archive directly instead of a store.** It gives up SPARQL and
  World's provenance-preserving graph.
