# 0041. World v1 builds a disposable, provenance-preserving graph

Status: accepted

World needs a first runnable projection before the rehearsal review application
can be ported. The archive's committed evidence is available on the V2 port
branch, but the store, rebuild procedure, and promotion contract are still open.
The first implementation must not turn a query result into archival authority.

## Decision

**Input is one committed snapshot.** A read-only `archive export` command emits
the archive identifier, specification, local context, exact cited contracts,
and complete record revisions from one Git commit. It emits no successful
snapshot when any required evidence is missing, malformed, or unsupported.
World never reads the worktree as evidence.

**The store is disposable.** World v1 projects that export into named RDF
graphs using an existing SPARQL engine. Oxigraph is the initial engine to test.
The engine's files live outside the archive's normative tree and can be
deleted. A rebuild of the archive-owned graph requires only committed archive
evidence and the local contexts and contracts copied with it. External graphs
may require another download; an unavailable source is reported as unavailable,
not as an empty answer. A completed build records its input commit and source
versions as regenerable build metadata.

**Claims remain attributable.** Record revisions, claims, assessments, pinned
references, and their agents remain individually identifiable in the graph.
PROV-O and Web Annotation supply the inherited meanings where they fit; an
Archive profile retains exact revision pins, contract citations, and the
separation of `by` from `agent`. No query silently picks a winner among
conflicting claims. A separate derived graph may contain convenient flat
relations only after the cited claim kind has an accepted current rule.

**The first outside source is narrow.** World may retrieve selected MusicBrainz
Works by identifier into a separately named, refreshable external graph. It
does not load the whole outside catalogue or infer identity from matching
titles. A correspondence worth keeping is a proposed Archive claim admitted
only through Door. The claim must pin its local subject, name the external
subject and mapping strength, and retain the source and asserting agent.
`skos:exactMatch` and `skos:closeMatch` are the candidate mapping terms under
ADR 0011; the contract must say how they apply to Archive and MusicBrainz
subjects before Door accepts them. `owl:sameAs` is never emitted.

**Queries do not govern.** SPARQL answers support exploration and application
reads. Policy and consequential transitions read Archive evidence, never a
World result. World v1 supplies a local query command; exposing it to a browser
requires a later application adapter and disclosure review.

## What this does not decide

This record does not define per-kind current rules, a MusicBrainz-to-LRM type
equivalence, or a general outside-data collection policy. Those remain
unsupported until their contracts or decisions are accepted. ADR 0042 defines
the first work correspondence and performance-to-work scope. Projected facts
confer no archival standing on their own.

## Alternatives considered

- **Store correspondences only in World.** Losing the store would lose an
  operator's assertion, violating INV-4 and INV-10.
- **Project only flat triples.** This discards disagreement, assessment,
  contract identity, and provenance required by INV-7 and INV-24.
- **Have World parse Archive's worktree directly.** It can combine uncommitted
  files with committed records and mistake an incomplete read for absence.
- **Implement an RDF store or query engine here.** An existing engine supplies
  the mechanism without creating another preservation contract.
