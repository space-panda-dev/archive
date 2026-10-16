# 0040. V2 audio-section-to-song claim route

Status: accepted

ADR 0036 requires a queryable semantic route from a reviewed V2 audio section
to a distinct song entity. It also requires exact source locators, pinned
artifact revisions, and evidence for any machine match. The byte bundle and
source census cannot provide that route by themselves. The source application
has its own review and selection vocabulary, but those records do not acquire
Archive meaning from similar field names or from the application's current
selector (ADR 0015, ADR 0035).

## Decision

Accept the contract set and boundary rules in
[0040-audio-section-song-route.md](../proposals/0040-audio-section-song-route.md)
as the basis for the first V2 audio semantic route, subject to shipping exact
canonical contract bundles and JSON-LD context fragments in one specification
release (ADRs 0025, 0027). The set comprises:

- `archive.audio/song_entity`, constraining a core entity to the Archive-local
  `song` kind and the `archive.audio/song` identity namespace;
- `archive.migration/audio_selection`, for ordered exact spans and pinned
  source artifact revisions;
- `archive.migration/audio_song_assertion`, for source-supported assertions
  that a selection is a distinct song; and
- `archive.migration/audio_song_match`, for machine-produced assertions
  with their scored run, method, input pins, and evidence origin.

The selection and song assertion are separate claims. The assertion refers to
one selection and one distinct song entity, and to the pinned migration bundle
revision plus exact source-record locator. A selected span uses ordered
half-open integer sample intervals and a positive sample rate (S-15). A source
time value is converted only when its source sample rate and boundary semantics
yield one exact integer sample offset; ambiguous conversions remain unresolved.

The review app owns first identification. For a new song it proposes a random
lowercase RFC 9562 UUIDv4 `song_key`, represented to Door as identity name
`song-<uuid>` in `archive.audio/song`. The app reuses the key for retries of the
same pending human review and does not recycle it after rejection. Door
validates the installed entity contract and derives the Archive object
identifier under ADR 0026. The title and machine score are evidence only; they
are not identity. Reuse of an existing song entity requires a human-reviewed
reference to that entity.

Migration identity names for source selections use
`v2-segment-<lowercase-hex-of-source-id-utf8>` only when the V2 schema and
accepted source audio decision establish that the source ID names the
enduring selection. Assertion identities use the source assertion ID only
when its source relation establishes enduring identity; otherwise they remain
unresolved. Paths, titles, bounds, digests, and match scores never supply
object identity (ADR 0021, ADR 0026).

A machine match records the score in the producer's scale, its distance
direction, method and version, rule, compared and corpus selection pins, and
the run's exact input pins. A saved report may be cited as source only if it is
captured and verified in the bundle; a recomputed score cites its actual run
and inputs. A lower-is-more-similar distance is neither probability nor
identity and does not determine review or currentness. A match remains a
machine claim alongside other claims (ADR 0005, INV-7, INV-8).

At the Archive boundary, `by` names the versioned migration program; `agent`
names the original person only when an accepted source mapping resolves that
person, otherwise the route is unresolved. Machine match claims identify the
scoring process as `agent`. Door supplies `at`. Every migrated claim cites its
installed contract, bundle artifact revision, source locator, and exact pinned
references as required by ADR 0022 and ADR 0035.

The migration route MUST preserve and account for suggested, rejected,
retracted, and superseded source rows without treating them as affirmative
current assertions. This decision defines an assertion translation, not a
per-kind current rule. The query returns every supported admitted claim,
conflicts, and incomplete references; a regenerable projection may index this
route but may not hide unresolved state. A bundle lookup alone does not make
the semantic route queryable.

This decision does not authorize a production write. Migration reports the
route unresolved until these contracts and the `song` entity kind are installed
by a specification release, source and artifact pins resolve, Door admits the
records, and read-after-admission verification confirms all locators and
boundaries.

## Why separate the contracts

Selection identifies the exact audio a consumer may use. The reviewed
assertion identifies a song entity for that selection. A machine match claim
asserts the same relation under its versioned rule while retaining its score
and method as provenance; it does not become a human assessment. Keeping the
meanings separate permits a query to retrieve exact audio even when the song
is unresolved, and permits disagreement about song identity without changing
the selection.

The random first-identity token gives the review app a stable proposal key
without treating a mutable label or score as identity. Door retains authority
over the portable Archive identifier and admission. This respects INV-2 and
does not require a global song registry.

## Alternatives rejected

- **Bundle lookup without claims.** It preserves bytes but cannot answer the
  required semantic query or provide Archive identity and pinned relations
  (ADR 0035, ADR 0036).
- **One claim that combines selection, song, and match.** It couples exact
  audio bounds to a potentially disputed identity and machine evidence.
- **Title or score as song identity.** Both can change or collide and violate
  ADR 0021.
- **Treat nearest match as reviewed or current.** A distance is method-specific
  evidence, not a probability, assessment, or currentness rule (ADR 0005,
  INV-7, INV-8).
- **Convert seconds through floating point.** It can move a boundary and
  violates S-15's exact media-boundary requirement.
- **Trust V2 current selection.** V2 supplies source evidence and mechanism;
  Archive owns claim and current semantics (ADR 0015).

## Consequences and remaining gates

A decision acceptance authorizes contract and implementation work after
the exact bundles are reviewed and released. The contract draft's JSON Schemas and
context fragments, its source-state mapping, the `song` kind declaration, and
the V2 source-ID premises must be made exact in the specification release.
The Archive-to-Library Reference Model mapping for song and MusicBrainz kinds,
all per-kind current rules, any ambiguous source boundary conversion, and the
general query interface remain open. Until the contracts are installed,
migration code cannot propose these claims and the semantic shadow cannot call
the route queryable (ADR 0035, ADR 0036, INV-24).
