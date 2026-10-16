# 0011. World is a projection

Status: accepted

The archive needs to relate its evidence to the wider world (which composition a
recording performs, which MusicBrainz entry that is, who played) and to answer
questions across all of it. The risk is that the query store quietly becomes the
real database and the archive degrades into blob storage.

## Decision

**World relates and queries; the archive owns.** World assembles the archive's
evidence into a graph, relates it to outside knowledge, and answers queries. The
archive stays authoritative for what it owns, even when World shows it. World
holds as little unique knowledge as practical; rebuilding it may be expensive
and need outside data, but it is always possible in principle.

**The test.** For any index or projection: if it vanished, where would the
authoritative information still be? If the answer is "nowhere", it has become
authoritative by accident, and that is a defect.

**Promotion.** Something World learns that is worth keeping (a correspondence to
a MusicBrainz work, say) is proposed to Door as a claim with its own source and
provenance. Entities the operator asserts are archive evidence; the imported
outside universe is World's. A correspondence is recorded as a SKOS mapping
(`skos:exactMatch`, `skos:closeMatch`), never as `owl:sameAs`.

**Claims stay claims in the graph.** World represents each claim as a resource
shaped like a Web Annotation: target, body, creator, generator, and its
assessments. Convenient flat facts ("the current song for this recording is
Paper Lanterns") live only in a separate derived graph built from "current", and nothing
writes them back.

**One definition of "current".** World materialises "current" by evaluating the
rule in each claim kind's contract, never with a separately written query.

**SPARQL is World's query language.** The engine is open. Datalog serves policy
over archive evidence; SPARQL serves exploration of World's graph and never feeds
a consequential decision directly.

**Usage enters as aggregates.** What people play or watch (Navidrome, Jellyfin)
may drive retention, so it enters the archive as periodic aggregate claims ("14
plays by 3 listeners in September, from Navidrome"). Raw logs stay outside.
Listeners are counted, not named, unless the operator decides otherwise. A
period with no data is unknown, not zero.

## Rejected

- **World as the source of truth.** The failure this record exists to prevent.
- **`owl:sameAs` in evidence.** One wrong match would merge two identities and
  everything said about them.
- **Recording every play.** Millions of records, and too much detail about other
  people.
