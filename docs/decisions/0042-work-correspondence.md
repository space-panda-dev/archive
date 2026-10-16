# 0042. A work correspondence is an attributable mapping claim

Status: accepted

World v1 needs to relate a song asserted by the operator to a MusicBrainz Work.
The rehearsal review application's `song_performance` records do not yet name
a durable work, and a title match does not establish one. ADR 0011 calls for
SKOS mappings, while the [SKOS reference](https://www.w3.org/TR/skos-reference/#mapping)
defines `exactMatch` and `closeMatch` between `skos:Concept` resources. A
MusicBrainz Work and an Archive work entity are works, not automatically
concepts in a vocabulary. Applying those properties
directly to the works would silently type both as SKOS concepts and weaken the
meaning Archive inherits (INV-11).

## Decision

**Assert the local work first.** A musical Work is an Archive entity, admitted
through Door under an installed entity-kind vocabulary. Its stable object ID
does not come from its title, artist, or MusicBrainz ID. A performance-to-work
claim is separate from a work-to-MusicBrainz correspondence. World v1 includes
the performance-to-work claim so it can relate performances across rehearsals;
this decision does not port the review application.

**Record the correspondence as a claim.** The mapping claim pins the local
work revision and carries the external MusicBrainz Work URI plus the SKOS
mapping term, limited to `skos:exactMatch` or `skos:closeMatch`. It names its
asserting agent and writing program in the envelope. A correction supersedes
the claim explicitly; a competing mapping remains visible for Review. The
claim's identity is an opaque, contract-owned name supplied once by its
proposer, independent of title and target URI. Door validates the installed,
closed contract before admission. World can propose this claim but cannot
commit it.

**Map catalogue concepts, not work identities.** World projects a local SKOS
concept for the Archive work's catalogue identity and another for the external
MusicBrainz Work's catalogue identity. The accepted claim produces the stated
SKOS mapping between those concepts, with a link back to its exact claim
revision. Each concept points to the work URI it describes. Neither work gets
an `owl:sameAs` triple, and the mapping never merges Archive object IDs.
`skos:exactMatch` may support broad retrieval, but its transitivity does not
become an Archive identity or currentness rule.

**Keep source provenance honest.** A person's judgment is sufficient for the
first correspondence; the claim records that person as `agent`. A World result
retrieved from MusicBrainz is only a candidate. If a specific external response
or other source is part of the justification, it must first be admitted as
evidence and cited by an exact reference. A URL and retrieval time in World's
cache alone are not durable provenance. Until content admission or a suitable
source-evidence contract exists, World does not auto-promote outside results.

**Show disagreement.** World v1 shows every supported performance-to-work and
work-to-MusicBrainz claim with its source and assessments. It does not choose a
winner or materialise a flat current relation. A later per-kind rule may define
`current`; until then a current answer is explicitly unsupported.

## Contract required before implementation

The specification needs an installed musical Work kind and versioned claim
bundles for performance-to-work and work-to-MusicBrainz correspondence, with
closed payloads and reference roles. The mapping bundle must constrain the
external URI to a MusicBrainz Work identifier, the mapping term to the two
SKOS values above, and the local subject to an exact pinned work revision.
The performance claim must pin a durable performance subject and an exact work
revision; an artifact or its filename cannot stand in for performance
identity. The bundles must say how their subjects and claim revisions are named
in the projection. The first contracts leave `current` undefined: asking for
one winning correspondence reports unsupported.

## Alternatives considered

- **Use `owl:sameAs` for the works.** One mistaken match would merge identities
  and everything said about them; ADR 0011 rejects it.
- **Put `skos:exactMatch` directly between Work URIs.** SKOS would treat the
  works as concepts, an unaccepted semantic change.
- **Keep a World-only correspondence.** Losing the store would lose the
  assertion, contrary to INV-4 and INV-10.
- **Infer the work from a performance title.** A title is neither stable
  identity nor enough evidence to distinguish arrangements and homonyms.
