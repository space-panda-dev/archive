# 0035. V2 migration preserves source evidence and admits supported claims

Status: accepted

A V2 migration must preserve what the source records said while making
supported meaning available in Archive's claim model. Translation alone can
lose source facts; raw bytes alone leave supported meaning unavailable to claim
consumers. The V2 census accounts for source files and fields, but does not
establish Archive identity, source references, or claim semantics.

## Decision

**A V2 migration preserves exact source record bytes as evidence and admits
Archive claims only for routes defined by accepted Archive contracts.** The
source bytes preserve the V2 evidence; claims express only meaning an Archive
contract defines.

For each source record considered by migration:

- Its original bytes MUST be retained in Archive content without reserializing
  the V2 JSON or JSONL.
- Malformed, unreadable, unknown-schema, and unsupported source units MUST stay
  explicit in the accounting report. A failed parse MUST NOT turn a unit into
  an absent record or a successful translation.
- A translated claim MAY be proposed only when an accepted, installed Archive
  contract defines its payload and references, and accepted rules define the
  Archive identities and source relationships it needs. Door remains the only
  writer of archival meaning.
- Source fields and relationships MUST be accounted for as translated under an
  accepted claim contract, retained only in raw evidence, or unresolved.
- A claim MAY cover a defined subset of a source record when an accepted
  contract defines that claim's scope and the claim is complete within that
  scope. An unmapped source field outside the claim's scope does not by itself
  invalidate that claim. If a missing mapping affects the claim's defined
  scope, or the claim would imply that the full source record was translated,
  that claim MUST NOT be proposed.
- Migration MUST report untranslated or unresolved fields and relationships.
  It MUST distinguish preservation of declared source bytes from completeness
  of claim routes, and MUST NOT report a route or overall claim translation as
  complete while required meaning remains unmapped. The report format remains
  open.

This chooses the preservation target, not the representation of raw evidence.
Whether raw bytes are admitted as one bundle artifact or as separate artifacts
remains open. So do identity names for raw evidence objects, Archive object
identity for translated claims, whether and how `source` identifies V2 records,
which source relationships become Archive references, and migration batch
grouping. None may be inferred from a path, filename, label, content digest, or
V2 identifier without an accepted rule (ADR 0021, ADR 0022).

The supported claim routes are open individually. Each populated V2 schema
needs accepted Archive contracts and identity/reference rules before its
records can produce claims. Similar field names, matching JSON types, or a V2
contract citation do not establish equivalence. A source record can have a
valid scoped claim and still have unresolved source fields; V2 current selectors
and review semantics do not fill those gaps (ADR 0015, INV-24).

Adding migration claim contracts requires a specification release that ships
their canonical schema and context bundles (ADR 0025, ADR 0027). Until those
contracts and the unresolved rules above are accepted, migration code MUST fail
closed before proposing unsupported objects or claims. This does not define
migration of an existing Archive between specification versions.
Content-bearing admission also remains behind the required INV-5 git-annex
experiment; this proposal does not waive that gate.

The matching specification amendment is a draft at
[0035-v2-migration-spec.md](../proposals/0035-v2-migration-spec.md). It is not
part of the current specification.

## Why preserve both forms

Raw evidence preserves exact source bytes and facts whose Archive semantics
remain unresolved. Claims let Archive consumers query meaning validated under
Archive's own contracts. Keeping both avoids making a lossy translation the
sole surviving account and avoids asking a query consumer to treat V2 payloads
as Archive claims.

This follows the record model: claims coexist with their provenance, contract
citations define their meaning, and unsupported reads remain explicit (ADR
0003, ADR 0005, ADR 0022, ADR 0027; INV-7, INV-11, INV-24). It also follows the
porting rule: V2 supplies source evidence and mechanism, while Archive decides
the meaning (ADR 0015).

## Alternatives rejected

- **Translated claims only.** A route that omits an unsupported field or
  relationship would make the translation the only retained evidence of that
  source record.
- **Raw source bytes only.** This preserves evidence but does not expose
  contract-supported meaning as Archive claims.
- **A generic opaque claim for every V2 record.** It would claim meaning
  without an accepted contract and turn source-schema presence into an Archive
  semantic assertion (ADR 0027, INV-11).
- **Unscoped best-effort partial claims.** They make omitted material fields
  look like complete claim meaning and violate the incomplete-read rule
  (INV-24). A contract-complete claim with an explicit narrower scope differs.

## Consequences

The source manifest and census are preflight accounting reports, not substitutes
for source-byte preservation or accepted semantic routes. Unless a later
contract admits a specific attestation, those reports are regenerable or
operational state, not archival evidence.
Migration can admit a contract-complete claim about a defined scope while
retaining other source fields only in raw evidence. Reports must keep source-byte
preservation separate from claim-route completeness. “Complete” does not assert
a V2 or Archive current rule.

The one-bundle versus per-file evidence layout, query projection, exact
contracts, identity allocation, source references, reference translation, and
claim-batch boundaries stay open. Code depending on any of these decisions
remains unsupported until the relevant decision and contracts are accepted.
