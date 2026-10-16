# 0027. Core payloads are closed; kind payloads use installed contracts

Status: accepted

The envelope proposal says `payload` is an object, while admission needs to
locate an artifact's content and transition dispatch needs concrete contract
bytes. Without a closed core payload, Door could accept a record whose content
cannot be verified or silently discard a legacy field. Without installed
contract bundles, a valid transition writer would be severed from its own
validator.

## Decision

Door gives `artifact` and `entity` the two smallest core payloads needed to
bootstrap and preserve bytes. Claim and assessment meaning remains in
archive-resident contracts.

### Artifact

An artifact payload is exactly:

```json
{"content": {"digest": "sha256:...", "size": 123}}
```

The digest and size are the content identity verified by admission. Location,
annex key, filename, source path, capture time, media type, origin, and whether
the bytes are preferred are not artifact payload fields. Each is a location,
provenance fact, or typed claim when a contract defines it. This lets several
objects share content without sharing identity (ADR 0003).

### Entity

An entity payload is exactly:

```json
{"kind": "person", "label": "optional human-readable label"}
```

`kind` is a non-empty term defined by the core context or an installed
controlled vocabulary. `label` is an optional non-empty string and is never
identity. The initial core context defines `person`, `organisation`,
`software`, `model`, and `process` so provenance can represent its agents.
Other entity-kind mappings, including the open MusicBrainz-to-LRM mapping,
remain unsupported until their vocabulary is installed; a caller may not
invent a kind string.

### Claims and assessments

A claim has at least one contract citation. An assessment also has at least one
contract citation and one or more exact `assessment_of` pins to claim revisions.
Every cited contract's schema validates the payload and the reference roles it
owns; all must succeed. Payloads are closed according to those schemas. The
envelope does not repeat a `kind` or `kind_version`: the contract identifier,
version, and digest are the type.

This proposal does not define annotation, enrichment, or assessment outcome
contracts, or any contract's current rule. Those records fail as unsupported
until their contracts are accepted. It defines the mechanism they must use,
not their domain semantics.

### Installed contracts

The specification distribution contains canonical bundles for exactly these
initial transition contracts:

- `archive.transition/operator_initialization`, version `1`;
- `archive.transition/retire`, version `1`; and
- `archive.transition/destroy`, version `1`.

Their JSON Schemas use JSON Schema 2020-12, are closed and self-contained, and
contain no remote `$ref`. Their context fragments follow ADR 0025. Their
schemas encode the payload and reference requirements in ADRs 0024 and 0026.
The bundle bytes, rather than a digest copied into program source, determine
their citations.

`archive init` verifies each packaged bundle's RFC 8785 digest and copies it to
the digest path proposed by ADR 0020 before creating a record that cites it.
Door loads a citation only by digest path, verifies the bytes again, then
checks that its identifier and version match the citation. A missing, changed,
unknown, or remotely dependent contract is an incomplete read or an admission
refusal, never an empty schema.

Adding contracts outside a specification release and defining per-kind current
rules remain open. The initial implementation therefore supports only the
three transition kinds above plus core artifact and person-entity admissions.

## Real-record boundary

The content-mismatch fixture is now expressible: admission compares the
artifact payload's digest and size with the locally verified staged bytes and
refuses either mismatch.

The AppleDouble fixture asserted that sidecar bytes were an original capture.
This core artifact payload makes no such assertion. Migrating its legacy
`occurrence`, source, name, or origin fields requires contracts and origin
values that are still open; a draft carrying them is refused as unsupported
rather than translated into a plain artifact. Deliberately archiving
AppleDouble bytes as bytes is not forbidden. Claiming what they represent
without a contract is.

## Alternatives rejected

- **Allow arbitrary artifact metadata.** Door could neither verify its meaning
  nor keep location out of revisions.
- **Put filenames and annex keys in artifact payloads.** They are mutable
  locations, not identity (INV-6).
- **Treat any entity kind string as valid.** That invents semantic mappings the
  context does not declare and weakens INV-11.
- **Repeat claim kind and version in the payload.** They could disagree with
  the pinned contract citation.
- **Treat a missing contract as an empty schema.** It converts an incomplete
  read into apparent success (INV-24).
- **Fetch schemas or `$ref` targets.** Validation would depend on a mutable
  network service and the archive would not stand alone.
- **Embed contract digests in Haskell source.** The installed bundle is the
  authority; duplicating its digest creates a severed writer/validator chain.
