# 0036. The first V2 migration is one frozen, accounted bundle

Status: accepted

ADR 0035 settles preservation of exact V2 source bytes and admits Archive
claims only under accepted Archive contracts. It leaves the evidence unit,
accounting format, and the first queryable routes open. The first migration
also contains routine job, attempt, and batch records whose bytes matter even
when they do not each warrant a separate Archive claim. Repeated failures can
be meaningful as one incident, while every retry remains part of the source
evidence.

## Decision

The first V2 migration packages the frozen V2 record tree as one evidence
bundle. The bundle is a deterministic POSIX PAX archive using USTAR headers,
with exactly these logical file entries:

```text
manifest.json
source/<original-relative-path>
```

Each source record remains a regular file with the exact source bytes. The
bundle does not reserialize source records or preserve filesystem ownership,
timestamps, permissions, or hard-link topology; those are storage metadata,
not record bytes. Each hard-linked path is stored as its own regular file with
the bytes read at that path. Symlinks and special files are refused, never
followed. The bundle is made from a frozen disposable copy, not a live V2
tree.

`manifest.json` is UTF-8 JSON in RFC 8785 canonical form, without a byte-order
mark or trailing newline. Its format identifier is
`archive.v2-migration-bundle/1`. It lists every `source/` file in ascending
UTF-8 byte order with its bundle member path, byte length, and `sha256:` digest.
It also lists every recognized source record as an exact byte locator within a
member: member path, byte offset, byte length, and digest of those record
bytes. It accounts for each unit as covered by a candidate route identified by
its exact Archive contract citation, preserved without a route, or unresolved
with a reason. It carries no bundle
digest, Archive object identifier, or claim revision identifier. Those values
would either be circular or would make the manifest depend on a later Door
transition.

The tar serialization uses POSIX USTAR headers with one local POSIX PAX
extended header for every logical file entry. All logical entries are sorted
by the UTF-8 bytes of the complete member path. Each PAX header contains only
the `path` record, even when the path would fit in a USTAR header. Its header
name is `PaxHeaders/<eight-digit-entry-number>`; the following file header name
is `PaxFiles/<eight-digit-entry-number>`. The PAX `path` record uses the POSIX
length-prefixed form, where the decimal length counts every byte in the record,
including the digits, separating space, key, equals sign, UTF-8 value, and
newline. This fixed pattern supports long path components without relying on
GNU extensions. Paths containing NUL or newline are refused.

All USTAR headers use mode 0644, uid/gid 0, empty owner and group names, mtime
0, regular-file type `0` for files, `x` for local PAX headers, zero device
numbers, magic `ustar\0`, and version `00`. The checksum is six octal digits,
NUL, and space, calculated with the checksum field set to spaces. File data and
PAX data are zero-padded to 512-byte blocks, and the archive ends with exactly
two zero blocks. No other PAX keys, global headers, GNU extensions, explicit
directory entries, or links are emitted. Entries whose file sizes exceed the
USTAR octal size limit are refused.

The bundle's **content identity** is `sha256:` over the complete tar byte
stream, including the manifest. Its Archive **object identity** is allocated
separately under namespace `archive.migration/v2-bundle`, with identity name
`first`. That fixed name denotes the one first-migration bundle in this
archive; it is not derived from a source path, content digest, or source ID.
The object ID follows ADRs 0021 and 0026. A retry with the same content is the
same object and content. If the fixed object already exists with different
content, migration refuses; it does not replace the frozen bundle in place.

The bundle contains V2 record files only. Original media content stays outside
it and is admitted as separate Archive artifacts so each source content keeps
its own content digest and artifact identity. Bundle preparation requires a
frozen source tree copied to a disposable staging tree. The existing bounded
scanner runs on that frozen copy before and after packaging, and both manifests
must agree. Its safe-open checks remain required: regular files only, no
symlink traversal, path-collision refusal, bounded file count and sizes, and
detection of changes during reads. If the record tree cannot be frozen this
way, preparation is incomplete. When a later migration checkpoint relates
records to separately stored media content, its capture must establish a
coherent point across all relevant source roots.

Preserving source files in one artifact MUST leave them individually available
to an authorized reader. A read-only lookup over the admitted bundle and its
manifest lists original relative paths and retrieves one file's exact bytes
after checking its length and SHA-256 digest. It MUST report a missing member,
digest mismatch, or unavailable bundle as an error. Any lookup index is
regenerable; it is not an Archive record. This is source-file access, not a
claim that every V2 record has an Archive identity or semantic query route.

The first semantic shadow MUST make supported V2 assertions about selected
audio and song identification available as Archive claims. A reader must be
able to query the assertion that a reviewed section of a recording is
identified as a song and follow it to the exact selected audio and V2 source
record bytes. Retrieving the raw file from the bundle is not a substitute for
that claim. The route needs a claim for the exact selection and a claim about
that selection for the song assertion, with pinned artifact revisions, ordered
boundaries, source locators, and assertion provenance. Suggested, retracted,
and superseded V2 rows cannot be silently treated as current assertions. The
song assertion links the selection to a distinct song entity. A V2 title and
cover-song match distance can support a machine-asserted link to that entity
when an accepted contract defines the evidence, matching rule, and provenance.
The current V2 match reports are disposable; a migrated link must cite a
verified saved report or a new scored run and its exact inputs, rather than
pretending the distance came from a record inside the bundle.
The distance is a similarity measure, not a probability or an entity ID; a
match does not erase a competing assertion or establish which claim is current.
The entity's stable identity cannot be derived from a mutable title or score
(ADR 0021). The review app owns the workflow that first identifies a song and
proposes its stable entity identity. It does not write Archive state: Door
validates and admits the entity and any resulting claims (ADR 0002). The
accepted route contract must define the identity allocation and how selection
bounds, source identities, revisions, and review states map. Until that
contract is accepted and installed,
the route is unresolved and the semantic shadow cannot report it as queryable.
The records-only inventory shadow can report its separate, explicitly
incomplete result.

### Capture note for the current source layout (non-normative)

The current V2 record tree is on ext4 and original media is on Btrfs. The new
Archive for this migration will be initialized at an operator-selected root
under `/home` on Btrfs; its Git store, records, contracts, and annex content
will all be under that root. The records-only checkpoint can quiesce V2 record
writers, copy the record tree, and verify the copy without taking a Btrfs
snapshot. A later full migration checkpoint that relates records to media
must establish one coherent capture across both source roots. Quiescing
writers and pinning originals with a read-only Btrfs snapshot is one part of
that procedure; a live scan across the filesystems is not a coherent snapshot.

The migration may propose queryable Archive claims for core content and
consequential failures only where an accepted Archive contract defines the
claim's complete scope, identity, and references. Repeated credential failures
that source evidence ties to one failure episode are represented by one
incident claim. Every original retry remains in the raw bundle and is
accounted for; aggregation does not replace or coalesce source records. A
recovery is a separate claim referring to that incident and to source evidence
of a successful recovery. The end of retries, a changed configuration, or an
absence of later failures does not establish recovery.

Every translated claim has a pinned `migration_source` reference to the exact
revision of the bundle artifact. Its accepted claim contract carries one or
more closed source locators: the bundle member path, byte offset, byte length,
and `sha256:` digest of the selected record bytes. A direct claim MAY also use
the envelope's `source` object to preserve one original V2 schema and source
identifier exactly. A missing or unstable V2 identifier is not invented from
the member path or digest; the pinned bundle reference and byte locator still
identify the evidence. An aggregate incident uses payload locators for all
attempts it covers rather than misusing the single-valued envelope `source`
field. A recovery claim has both a pinned incident-claim reference and a
locator for the positive recovery evidence.

Archive provenance is assigned at the boundary. `by` identifies the
versioned migration program using its canonical Package URL. For a claim that
translates a source assertion, `agent` is the original asserting agent only
when an accepted mapping identifies that agent as an Archive entity or
portable agent; an unmapped source agent leaves that route unsupported. For an
Archive assertion created during migration, such as grouping attempts into an
incident or verifying preserved bytes, `agent` identifies the migration
process that made that assertion. `at` is Door's admission time. Original V2
writer, agent, and event-time values remain in the raw record bytes and are
carried into claim payloads only under the route contract; they never replace
Archive `by`, `agent`, or `at`.

The bundle object uses the one fixed identity allocation above. Translated
claim identities use a migration-owned namespace and a source identifier as
identity name only when an accepted route contract establishes that the source
identifier denotes the enduring Archive object. Otherwise the route must define
another stable semantic identity or remain unresolved. Source member paths and
content digests are locators, never object identity.

Before admission, every recognized source record is accounted for as one of:

- a candidate for a claim under an accepted route with a defined scope;
- preserved in the bundle without an Archive claim; or
- unresolved, with the reason recorded.

The manifest MUST reconcile these dispositions with its record locators. A
candidate is not represented by a claim until Door admits that claim and a
later check verifies its source locator and defined scope. A file without a
recognized V2 schema, including an operational summary, remains in the bundle
as exact bytes; the manifest marks its record inventory unresolved rather than
guessing that it contains zero records or assigning it a claim route. When any
file's record inventory is incomplete, the manifest MUST say so and MUST NOT
imply a complete source record count.
Unreadable, malformed, changing, or omitted source material makes preparation
incomplete; it cannot be counted as absent. A complete byte inventory does not
mean all source meaning has a claim route.

The preparation report may help construct and verify the bundle, but an
external report is not a substitute for the in-bundle manifest or source
bytes. The bundle and manifest are admitted through Door as evidence. The
preparation tool has no authority to create Archive records or write archival
state.

## Remaining open questions and implementation boundary

Acceptance settles the container, manifest encoding, bundle identity
allocation, member locators, and the scope of one frozen first bundle. It does
not accept the exact contracts and provenance fields for the manifest,
core-content route, incident claim, or recovery claim. The source relationship
rules that group retries into one incident, the minimum evidence that
establishes a common episode, the recovery success criterion, and the identity
rule for each translated claim also remain open. No route may infer these
details from filenames, timestamps, similar payload fields, or a V2 identifier.
Until accepted contracts and identity rules settle them, preparation may report
candidates but MUST NOT propose those claims.

The accepted bundle identity names the first frozen V2 import in an Archive.
Whether a later independent V2 source can be admitted to the same Archive needs
a separately accepted identity and accounting rule. Records added to the live
V2 source after this frozen first bundle are outside its scope. The bundle MUST
NOT be silently replaced or treated as covering those later records; a later
import needs a separately accepted identity and accounting rule.
The PAX format is selected because the source tree has a path component longer
than USTAR's name field; a USTAR-only design would reject that valid source
layout even though its whole relative path can be represented by PAX. The
current read-only inventory measured a 201-byte maximum component and a
212-byte maximum relative path.

These boundaries preserve ADR 0035's fail-closed rule and INV-24. A route that
cannot establish its complete declared scope remains unresolved; it does not
become a generic claim for the whole V2 record.

## Alternatives considered

- **One Archive artifact per source file.** This makes every record directly
  addressable but leaves the first migration's evidence unit and identity
  allocation to a path-by-path convention. It is not the proposed first
  migration layout.
- **Put original media bytes in the record bundle.** This would make one tar
  digest stand in for many distinct source contents. The accepted record model
  keeps content identity separate from artifact identity; each original stays
  a separate Artifact and uses its own verified digest.
- **One translated claim per retry.** This multiplies claims for retries that
  share one evidenced incident and risks presenting each retry as a separate
  failure episode. The raw records still preserve each attempt.
- **Incident only, with raw retries omitted.** This loses exact operational
  evidence and is prohibited by the byte-preservation decision.
- **Infer recovery from silence.** Missing later failures can mean a missing or
  unread source interval. Recovery requires positive source evidence.
- **Treat a complete manifest as complete translation.** Byte accounting and
  claim-route completeness answer different questions.

## Consequences

The first migration has one frozen evidence package with a deterministic
content digest and a separate Archive object identity. A reader can locate and
re-hash an exact source record without parsing or rewriting it. Query consumers
can use only the accepted claim routes. Routine source records remain
preserved even when they have no route. The exact contract decisions must
precede any production bundle or claim write.

The matching draft amendment is
[0036-v2-frozen-migration-bundle-spec.md](../proposals/0036-v2-frozen-migration-bundle-spec.md).
It is not part of Archive 0.0.2.
