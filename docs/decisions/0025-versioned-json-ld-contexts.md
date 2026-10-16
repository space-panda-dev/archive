# 0025. JSON-LD contexts are local and versioned with their contracts

Status: accepted

An archive must explain the meaning of every field without a network service,
and old records must not acquire new meaning when a vocabulary changes. The
core envelope and a claim kind evolve at different rates, so one global mutable
context cannot version both safely.

## Decision

Every Archive specification version owns exactly one immutable core JSON-LD
context at `spec/<version>/context.jsonld`. A record's `spec` value selects
that context. Records do not contain an `@context` field, and readers never
fetch a context or vocabulary from the network. External vocabulary IRIs in a
local context are identifiers, not retrieval instructions.

The core context declares the meanings of the envelope, four shapes,
references, and provenance. Contract fragments declare their own payload and
relation-role terms, including transition terms. The core context is part of
the specification version: changing any mapping requires a new
specification version and a new copied specification directory. A new version
does not reinterpret an old record; the old local context remains available.

Each contract is one closed JSON bundle containing exactly:

```json
{
  "id": "archive.transition/retire",
  "version": "1",
  "schema": {},
  "context": {}
}
```

`schema` is the JSON Schema that validates the contract-defined payload and
relations. `context` is the JSON-LD fragment that maps only the fields the
contract owns. It is an object of term definitions, not a nested or remote
context: it contains neither `@context` nor `@import`, and no term definition
selects a remote context. A fragment may not redefine a core envelope term or
a term owned by another cited contract.

The contract digest is SHA-256 over the RFC 8785 canonical form of the entire
bundle, including identifier, version, schema, and context. The file in
`contracts/sha256-<digest>.json` contains exactly those canonical bytes. A
record's citation repeats the bundle's `id`, `version`, and digest; all three
must match. Any change to a schema or context requires a new contract version
and digest. Reusing a version for different bytes is refused.

JSON-LD processing explains and exports meaning; it does not validate
admission. Door validates the closed JSON envelope and JSON Schemas directly,
without expanding JSON-LD. A reader that lacks the selected core context or a
cited contract bundle reports an incomplete read rather than an empty result
(INV-24).

For interpretation, a reader starts with the object inside the core context's
top-level `@context`, then considers contract fragments in the order their
citations appear in the record. No term may occur in more than one of those
objects, so order cannot change a mapping; a collision is an admission refusal
or an incomplete read. Context processing is confined to these local objects.
An imported vocabulary term must be constrained by an Archive profile or used
only as a mapping; a context mapping never imports weaker semantics (ADR 0012,
INV-11).

Migration never assigns Archive meaning merely because a predecessor field has
the same spelling. It selects a context or contract only where a translation
rule establishes the meaning; otherwise it reports the record unresolved and
retains the source evidence. Specification migration itself remains open.

## Why two version boundaries are enough

The core context changes with the core record language. A contract fragment
changes with one payload language. The record already pins both boundaries via
`spec` and contract citations, so a third context version would duplicate
identity without adding information. One digest over schema and context also
prevents a valid schema from being paired with a different semantic mapping
(S-12, S-13, INV-11).

The predecessor provides evidence that portable contract identifiers,
versions, and digests survive independent writers and migration. It did not
carry an archive-resident JSON-LD contract, so field-name similarity alone is
not migration evidence.

## Alternatives rejected

- **Fetch contexts by URL.** A network or mutable publisher could change what
  an old record means, violating the stand-alone archive requirement.
- **Embed a complete context in every record.** It duplicates stable material
  in revision identity and invites nearly identical variants.
- **Use one permanent global context.** A changed mapping would silently
  reinterpret old records.
- **Version context separately from specification and contract.** The record
  already pins those two semantic owners; another independently moving version
  permits mismatched combinations.
- **Digest schema and context separately.** It allows readers to combine two
  individually valid but mutually inconsistent halves.
- **Let a contract redefine envelope terms.** It would weaken the core's
  closed meaning and violate INV-11.
- **Use JSON-LD expansion as validation.** Open-world interpretation does not
  replace Door's closed, fail-fast validation (S-13, ADR 0012).
