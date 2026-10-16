# 0012. Inherit meaning; keep Archive's contracts

Status: accepted

Established vocabularies already name most of what the archive's records say.
Maintaining private synonyms for them is cost without benefit. Adopting them
wholesale would hand the archive's stricter rules to looser general ones.

## Decision

**Meaning in the records, machinery outside.** Records stay plain JSON with
Archive's field names, and Door validates plain JSON. The specification ships a
versioned **JSON-LD context**, copied into every archive and never fetched from
the network, that maps each field to its standard meaning: `by` to
`oa:generator`, `agent` to `oa:creator`, pinned references to PROV relations.
Someone holding only the archive can learn what every field means.

**The vocabularies.**

- **PROV-O** for provenance. Every transition takes PROV's shape: an activity
  that used inputs and generated outputs, agents in named roles (author,
  authorizer, committer), and the policy revision as its plan.
- **Web Annotation** for claims and their targets.
- **SKOS** for controlled vocabularies: assessment outcomes, entity kinds,
  transition kinds, agent roles. These are data files that ship with the
  specification; a two-value flag with no aliases may stay a plain value.
- **Entity kinds** follow the IFLA Library Reference Model (Work, Expression,
  Manifestation, Item), with MusicBrainz kinds (Work, Recording, Release,
  Artist) for music. A composition is a Work; a vinyl copy on the shelf (an
  entity) and its FLAC rip (an artifact) both map to Item. Other domains add their own specialisation when they
  arrive.
- **Media Ontology and Dublin Core** for payload fields where they fit.

**Identifiers outlive hosts and algorithms.** An archive receives a UUID when it
is created. Object identifiers are built from it without any hostname. Content
identifiers are equivalent to RFC 6920 `ni:` URIs, which carry their hash
algorithm. People and organisations are entities the archive can reference;
software and models are identified by Package URL where one exists.

**PREMIS and OAIS are mapped, not adopted.** The specification carries a PREMIS
Level 1 mapping: a documented equivalence of archive fields to PREMIS. An
artifact is a PREMIS File; multi-file renditions and tracks are claim kinds
mapped to Representation and Bitstream. Transition kinds map to Library of
Congress preservation events (retire to deaccession, destroy to deletion,
routine checks to fixity check). OAIS packages correspond without renaming: a
staging host's proposal is a submission package, the archive is the archival
package, and views, publication, and World are dissemination packages.

**Import discipline.**

- Reuse before inventing: a local term needs the new-noun test and a reason no
  imported term will do.
- Never accept a weaker contract silently: an imported term either gets
  constrained by an Archive profile or appears only as a mapping.
  `prov:Invalidation` is not retire or destroy; `oa:Annotation` does not promise
  a human author; `odrl:Policy` is not Archive policy; a bare triple is not a
  claim.
- Adopt structure, not verdicts. A term that carries a judgment the operator
  rejects (subject headings have carried slurs) may be replaced by a local
  concept, mapped to the original with `skos:closeMatch` or not at all, with
  the reason recorded.
- Always prefix the false friend: `arch:Entity` is a subject the operator
  asserts; `prov:Entity` is almost anything.

Vocabularies for other domains (DCAT, ODRL, SOSA, Data Cube) are adopted when
their domain actually appears.

## Rejected

- **Records as RDF documents.** Door would depend on open-world RDF semantics,
  which do not fail closed.
- **PREMIS as the record model.** It would import PREMIS's general rules in place
  of the archive's stricter ones, and it is built for bytes only.
- **Hostnames or bare hex digests in identifiers.** Neither survives twenty
  years.
