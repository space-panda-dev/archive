# Draft specification amendment: V2 source evidence and claims

Status: proposed; not part of Archive 0.0.2

A future specification release may adopt the following requirements. Adoption
requires the accepted decisions and canonical contract bundles described by
ADR 0035 and ADR 0027. This draft does not change the current specification or
make migration supported.

## V2 source evidence and claim migration

A migration from V2 MUST preserve the exact bytes of every declared source
record in Archive content. Parsing and translation MUST NOT replace or
reserialize those bytes. The raw evidence MAY be represented as one bundle
artifact or as separate artifacts; an accepted decision MUST define the
selected evidence unit and its identity rule before implementation.
Malformed, unreadable, unknown-schema, and unsupported source units MUST be
reported explicitly. A failed parse MUST NOT be treated as absence or success.

A migration MAY propose an Archive claim for a defined scope of a V2 source
record only when an installed Archive contract defines the complete payload and
relations for that scope, and accepted rules define all object identities and
source relationships used by that claim. A source field outside that scope MAY
remain only in raw evidence without invalidating the scoped claim. If an
unmapped field or relationship affects the claim's defined scope, or the claim
would imply that the full source record was translated, migration MUST NOT
propose that claim. Migration MUST account for source fields and relationships
as translated under a contract, retained only in raw evidence, or unresolved.
It MUST report unresolved meaning and distinguish preservation of declared
source bytes from completeness of claim routes; the report format remains
open.

A V2 identifier, path, filename, matching digest, V2 contract, or V2 current
selector MUST NOT by itself establish Archive object identity, a source
reference, a claim meaning, or currentness. Such meaning requires the
applicable accepted Archive contract and identity rule. An unresolved route is
an incomplete result under INV-24.
