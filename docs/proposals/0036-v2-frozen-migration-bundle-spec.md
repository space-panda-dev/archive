# Draft specification amendment: one frozen V2 migration bundle

Status: proposed; not part of Archive 0.0.2

This draft proposes requirements for the first migration from V2. It is not a
supported migration protocol. Acceptance requires the decisions and exact
contract bundles identified in
[ADR 0036](../decisions/0036-v2-frozen-migration-bundle.md), plus the
specification release that installs those contracts.

## Frozen bundle and manifest

The bundle MUST be one deterministic POSIX PAX archive using USTAR headers,
with exactly one
`manifest.json` entry and one `source/<original-relative-path>` entry for each
source file. It MUST contain every declared V2 record, including routine job,
attempt, batch, and run records. Each file's bytes MUST be copied exactly; a
parser or translator MUST NOT serialize a replacement. The bundle does not
preserve source ownership, timestamps, permissions, empty directories, or
hard-link topology. Each hard-linked path is a separate regular-file entry.
Symlinks MUST NOT be followed, and symlinks or special files MUST make
preparation incomplete.

The archive MUST contain a POSIX PAX local extended header (`x`) immediately
before every logical file entry, including the manifest. Each local header
MUST contain exactly one PAX record, `path`, with the logical member path.
The PAX record MUST use the POSIX length-prefixed syntax. Its decimal length
MUST count its own digits, the separating space, `path=`, the UTF-8 value, and
the terminating newline. This local header MUST be emitted even when the path
fits in USTAR's name and prefix fields. No global PAX header or other PAX key
may be emitted.

Logical entries MUST be sorted by the UTF-8 bytes of the complete member path.
The PAX header name MUST be `PaxHeaders/<entry-number>` and the following
regular-file header name MUST be `PaxFiles/<entry-number>`. Entry numbering
starts at `00000000` for `manifest.json` and increases by one in logical entry
order, using exactly eight decimal digits. Header fields MUST be fixed as
follows: mode 0644, uid and gid zero, empty owner and group names, mtime zero,
type `x` for the PAX header and `0` for the file header, zero device numbers,
magic `ustar\0`, version `00`, and a checksum encoded as six octal digits, NUL,
and space after summing a header whose checksum field is spaces. The USTAR
name field contains the stated short placeholder and its prefix field is empty.
Member data and PAX data MUST be zero-padded to 512-byte blocks. The archive
MUST end with exactly two zero blocks. A source file larger than the USTAR
octal size limit MUST be refused.

Member paths MUST be relative UTF-8 paths without empty, `.` or `..`
components, leading slash, NUL, or newline. The source spelling MUST be
preserved without Unicode normalization. Names that collide after NFC
case-folding MUST be refused. The local PAX path value supports long path
components that the USTAR name field cannot represent. These rules make
extraction location-independent and preserve the source path spelling without
deriving any Archive identity from it.

`manifest.json` MUST be UTF-8 JSON serialized in RFC 8785 canonical form, with
no byte-order mark and no trailing newline. Its closed top-level object MUST
contain exactly:

```json
{
  "format": "archive.v2-migration-bundle/1",
  "source_file_count": 1,
  "record_locator_count": 1,
  "record_inventory": "complete",
  "total_source_bytes": 12,
  "files": [],
  "records": []
}
```

The values above are illustrative. `files` MUST contain one closed entry per
source file with exactly `member`, `bytes`, `sha256`, and
`record_inventory`. `member` MUST be the full tar member path beginning
`source/`; entries MUST be sorted by their UTF-8 member-path bytes. `sha256`
MUST use Archive's `sha256:` spelling and cover the exact member bytes.
`record_inventory` MUST be either `{"status":"complete","count":N}` or
`{"status":"incomplete","reason":"<code>"}`. A complete inventory has
the exact number of source record units found by its schema parser, including
zero for a known non-record file. An incomplete inventory means the raw file
is preserved, but record count or boundaries could not be established. The
absence of a recognized V2 schema does not establish a zero count or a
non-record classification; such a file MUST have an incomplete inventory and
remain listed with its exact bytes.
top-level `record_inventory` is `complete` only when every file entry is
complete; otherwise it is `incomplete`. The bundle MAY still preserve all
source bytes when the source-record inventory is incomplete, but migration
MUST report that boundary and MUST NOT claim complete record or claim-route
accounting.

`records` MUST contain one closed entry per located source record with exactly
`member`, `member_sha256`, `offset`, `length`, `sha256`, and `accounting`, plus
`source` when the source schema provides an exact stable identity. Record
entries MUST be sorted by member path bytes, then ascending byte offset, length,
and digest. `offset` and `length` count bytes from the start of the member; the
record digest covers precisely that byte range. For a JSON Lines record the
range excludes its line terminator; the terminator remains preserved in the
containing member. `record_locator_count` MUST equal the number of entries in
`records`; when top-level `record_inventory` is complete, it MUST also equal
the sum of per-file counts.

The optional manifest `source` object MUST contain the exact source schema
identifier and source identifier as `schema` and `id`, with the exact V2
revision identifier as `revision_id` when one exists. It MUST NOT contain an
Archive object identifier. A path or content digest MUST NOT be used as a
substitute source identifier.

`accounting` MUST be one of these closed forms:

```json
{"status":"candidate_route","contract":{"id":"archive.claim/example","version":"1","digest":"sha256:<64 lowercase hex>"}}
{"status":"raw_only"}
{"status":"unresolved","reason":"unsupported_source_schema"}
```

`candidate_route.contract` MUST be the exact accepted Archive contract
citation containing `id`, `version`, and `digest`. It means that a complete
claim is eligible to be proposed under that contract, not that Door has
admitted one. The post-admission verifier MUST confirm every
candidate route against claims that cite the exact bundle revision and source
locator. It MUST reject an omitted, extra, or mismatched route. This avoids
putting claim revision IDs into the bundle manifest: they would depend on the
bundle revision, whose content digest includes the manifest.

`source_file_count` and `total_source_bytes` MUST equal the exact counts and
sum represented by `files`; every source file MUST be listed once. Every
located record unit MUST be listed once. A file may contain multiple units. A
malformed or unrecognized file remains listed in `files`; if record units
cannot be localized, its `record_inventory` MUST be incomplete with a reason.
A missing, unreadable, changing, or unlisted source file makes preparation
incomplete and MUST NOT be reported as absent.

One source record MAY support more than one scoped claim only when the
applicable contracts define those claims. Multiple retries may share one
incident route, but every retry remains a separate record locator and is
counted once in the source-record total. A complete inventory MUST NOT be
reported as complete semantic translation.

An authorized reader MUST be able to list the bundle's original relative
source-file paths and retrieve the exact bytes of one selected file through a
read-only lookup. Retrieval MUST use the admitted bundle and manifest, verify
the selected member's byte length and SHA-256 digest, and refuse a missing
member, unavailable bundle, or mismatch. A lookup index MAY be regenerated
from the manifest; it MUST NOT become archival state or create one Archive
object per source file. This lookup does not supply a semantic query route for
records without admitted Archive claims.

The bundle's content identity is SHA-256 over the complete PAX/USTAR byte stream,
including `manifest.json`, using Archive spelling `sha256:<64 lowercase hex>`.
The manifest MUST NOT contain the bundle digest, its own digest, or any Archive
object or revision identifier. The content digest therefore has no circular
dependency and protects both the manifest and all source members.

The bundle artifact's object identity MUST be allocated under namespace
`archive.migration/v2-bundle` with identity name `first`, following ADRs 0021
and 0026. The name denotes the one first V2 migration bundle for the Archive;
it MUST NOT be derived from a source path, filename, source ID, or content
digest. Door MUST refuse an existing object under this name if its content
differs from the exact retry. This proposal assumes only one first V2 source
per Archive; a second independent V2 source needs an accepted identity rule.
Records added to the live V2 source after this frozen bundle are outside its
scope. A later import MUST use a separately accepted identity and accounting
rule; it MUST NOT replace this bundle or claim that it covers later records.

The bundle MUST be prepared only from a frozen source tree copied to a
disposable staging tree. A live scan that may overlap source writes MUST NOT be
treated as a snapshot. The existing bounded source scanner MUST run before and
after packaging, and the two complete file inventories MUST agree. A scanner
failure, manifest mismatch, unsupported file type, or source change MUST
refuse bundle preparation. Original media content MUST remain separate from
this record bundle and enter Archive as its own content under the existing
artifact admission rules. A later migration checkpoint that relates record
files to content files from other source roots MUST establish a coherent
capture across all those roots.

The bundle and its manifest are evidence and MUST enter archival state only
through Door. A preparation or verification program MUST NOT write records,
content, refs, or other archival state. Its output report outside the bundle is
operational state and MUST be safe to lose.

## Queryable routes

Migration MAY propose claims for core content and consequential failures only
when installed Archive contracts define the complete claim scope, payload,
identities, provenance, and references. Source schema presence, a matching
field name or JSON type, a filename, a timestamp, or a V2 identifier MUST NOT
establish an Archive identity or claim meaning.

The first semantic shadow MUST include a queryable route for supported V2
assertions identifying a reviewed audio section as a song. The route MUST
represent the exact audio selection and a separate song assertion about that
selection as Archive claims, with pinned source artifact revisions, ordered
boundaries, source locators, and assertion provenance. The song assertion MUST
refer to a distinct song entity under an accepted contract. A machine assertion
MAY use a V2 title and a cover-song match distance as evidence for that link,
but MUST retain the matched corpus selection, the V2 source locators for
underlying preserved records, the scored run's input pins, algorithm and
version, distance direction and value (lower means more similar for the V2
matcher), and the rule used to propose the link.
The current V2 cover-song reports are disposable rather than V2 record files;
their values cannot be claimed to come from the record bundle unless they are
separately captured and verified. A recomputed score MUST cite its actual run
and inputs instead of inventing a V2 source locator.
The distance MUST NOT be represented as a probability, used as entity identity,
or treated by itself as a current or operator-reviewed conclusion. Entity
identity MUST NOT be derived from a mutable title or score (ADR 0021). The
review app MUST own the workflow that first identifies the song entity and
proposes its stable identity; Door MUST remain the only authority that admits
it. The contracts MUST define identity allocation, exact boundary
representation and reference relationships, and the treatment of suggested,
retracted, and superseded source rows. A bundle-file
lookup alone MUST NOT satisfy this route. Until those contracts are installed
and the admitted claims are verified against their source locators, migration
MUST report the route unresolved and MUST NOT call the semantic shadow
queryable. This requirement does not prevent a separately labeled records-only
inventory shadow.

When accepted source relationships establish that repeated credential failures
belong to one incident, migration MUST represent that episode as one incident
claim under its accepted contract. Each raw retry MUST remain byte-for-byte in
the bundle and be separately accounted for. The incident claim MUST carry a
source locator for every retry required by its contract; it MUST NOT replace
the source records or present one retry as the complete source evidence.

A recovery MUST be a separate claim referring to the incident and to positive
source evidence of recovery under its accepted contract. Silence after a
failure, cessation of retries, or a configuration change alone MUST NOT count
as recovery. If grouping or recovery cannot be established under the
applicable contract, migration MUST report the route as unresolved and MUST
NOT propose the claim.

## Source-to-Archive provenance and remaining boundary

Every translated claim MUST carry a `migration_source` reference whose `id`
is the bundle artifact object ID and whose `revision_id` pins the exact bundle
artifact revision. Since an Archive reference targets an Archive object, a
member path MUST NOT appear as a reference `id`. The claim contract MUST pair
the pinned bundle reference with this closed payload locator:

```json
{
  "member": "source/records/item.json",
  "member_sha256": "sha256:<64 lowercase hex>",
  "offset": 0,
  "length": 12,
  "sha256": "sha256:<64 lowercase hex>"
}
```

The locator's member digest MUST match the manifest file entry, and its record
digest MUST match the selected byte range. A single-source claim MAY also set
the envelope's `source` to the exact V2 schema identifier and source ID, with
the source revision ID when provided. If no stable V2 identity exists, the
claim MUST omit `source`; it MUST NOT synthesize an ID from path or digest. An
incident claim over multiple attempts MUST carry a locator array in its
contract payload and MUST NOT force a single V2 identity into the envelope's
single `source` value. A recovery claim MUST carry its successful source
locator and an exact pinned reference to its incident claim.

The Archive `object_id` for the bundle MUST be the Door-derived UUIDv5 for the
accepted namespace/name allocation above. A translated claim MAY reuse a V2
source ID as its identity name only if its accepted route contract says that
the identifier denotes the enduring Archive object. Otherwise the route MUST
define another stable semantic identity or remain unresolved. Neither source
paths nor member or bundle digests are object identity.

Each translated record's `by` MUST identify the versioned migration program
by its canonical Package URL. A direct translation's `agent` MUST be the
original asserting agent only when an accepted mapping identifies it as an
Archive entity or portable agent. If the source agent cannot be mapped, that
claim route MUST remain unsupported; it MUST NOT substitute a path or an
unrelated generic agent. For an Archive assertion created during migration,
such as grouping attempts into an incident or verifying preserved bytes,
`agent` MUST identify the migration process that made the assertion. Door MUST
stamp `at` as the Archive recording time. Original V2 writer, agent, and
event-time values MUST remain in the bundle and MAY also be projected into
claim payloads only as the route contract defines them.

The exact manifest and route contract IDs, their schemas and contexts, the
identity rule for translated claims, the source relationships that group
retries, the evidence establishing a common incident, and the positive
recovery criterion still require accepted decisions and contracts. The
proposed format does not authorize preparation from production data or an
Archive write. Until those details are accepted, a tool MAY verify a synthetic
candidate bundle, but MUST NOT create Archive records or report unsupported
routes as successful. An incomplete inventory or unsupported contract is an
incomplete result under INV-24.
