# Archive Specification

Version: 0.0.10
Status: draft

This is the contract an archive is governed by. It is copied into every archive
when the archive is created, so it has to make sense to someone who has only the
archive and not this source repository. The version above must match
[`VERSION`](VERSION). Older copied specifications remain immutable.

This specification is dedicated to the public domain under CC0 1.0. Anyone
may copy, implement, or change it, and an archive that carries it owes nothing
to anyone for doing so.

The key words MUST, MUST NOT, SHOULD, and MAY are used as in RFC 2119.

This draft is deliberately small. It states what is settled, marks what is
provisional, and lists what is not yet defined.

## 1. Terms

- **Archive**: a durable collection of evidence governed by this specification.
- **Evidence**: anything whose loss would lose information, including derived
  results deliberately kept.
- **Regenerable state**: anything that can be rebuilt from evidence.
- **Archival state**: evidence, and the transitions that change its meaning. It
  is distinct from storage (where bytes physically sit), operational state,
  external systems, and regenerable state.
- **Door**: the single authority through which archival state changes.
- **Operator**: the person who holds governance authority over an archive.
- **Governance authority**: authority to cause a consequence. **Evidentiary
  authority**: how strongly a claim should be believed.
- **Record**: one immutable revision of an object, in one of four shapes:
  artifact, entity, claim, or assessment.
- **Object**: the enduring thing a record describes, named by a stable opaque
  identifier.
- **Content**: bytes, identified by digest. Several objects may share content.
- **Location**: where content currently sits.
- **Claim**: a typed, versioned statement about other objects, with provenance.
- **Assessment**: a judgment of an exact claim revision, or a settlement between
  competing claims.
- **Admission**: bringing a new object into the archive through Door.
- **Transition**: any other change to archival state recorded by Door.
- **Retire**: a transition taking an object out of the collection; its records
  remain.
- **Destroy**: a transition withdrawing content from the archive's custody and
  requiring its deletion from every managed copy.
- **Policy**: a versioned declaration of desired archival state, authorized by
  the operator.
- **Failure domain**: a set of locations likely to fail together.
- **Managed copy**: a copy the archive tracks, can verify on demand, and can
  drop. **Observed copy**: a copy the archive can verify but does not control.
  **Projection**: another view of the same bytes. **Source**: where content came
  from before admission.
- **Disclose**: let content leave the operator's custody in a form others can
  read. **Publication** is
  disclosure that others can see. A **processor** is a destination that
  receives content to compute on it and shows it to no one else.

## 2. Requirements

### The archive

**S-1.** An archive MUST identify the version of this specification that governs
it.

**S-2.** Archival state MUST change only through Door. A change to archival state
made any other way is a defect, not a new valid state. Storage MAY change without
Door, but only to carry out what Door has sanctioned (a policy revision or a
recorded transition).

**S-3.** An archive MUST have at most one active operator at a time. A change of
operator MUST be recorded as a transition.

**S-4.** Repeating an operation whose inputs have not changed MUST NOT change
durable state: no new records, commits, or content-location entries.

**S-5.** Regenerable state MUST NOT be the only copy of any evidence, and its loss
MUST NOT be archival loss.

**S-6.** Program configuration is not policy and is not governed by this
specification.

**S-7.** Requirements about content, copies, destruction, and placement apply to
digital content. A physical item is recorded as an entity admitted to and retired
from the collection; its physical custody is outside the archive.

### Records

**S-8.** Every record MUST have exactly one of the shapes artifact, entity,
claim, or assessment. A record revision MUST NOT be altered after admission; a
change is a new revision. Every transition MUST be recorded as a claim whose
kind names the transition; a policy revision is an artifact holding the rules
text, adopted by a claim of kind `policy_revision`.

**S-9.** An object identifier MUST NOT be derived from content and MUST NOT change
when the object's name, location, or content changes. Content MUST be identified
by a cryptographic digest. A change of location MUST NOT change any identity and
MUST NOT create a new revision.

**S-10.** Supersession MUST be explicit. It MUST NOT be inferred from timestamps,
names, digests, or locations.

**S-11.** Every record MUST identify the program that wrote it and the agent that
asserted it. Provenance MUST be carried by the record's envelope and references,
not repeated in its payload.

**S-12.** A claim MUST cite the contract (identifier, version, digest) that
defines its payload. Contract definitions MUST be available within the archive.

**S-13.** The meaning of every envelope and payload field MUST be declared by the
JSON-LD context stored in the archive with this specification. Validating an
admission MUST NOT depend on processing that context.

**S-14.** No identifier (of an object, content, archive, or agent) MAY contain a
hostname.

**S-15.** Media boundaries MUST be stored exactly, for example as integer sample
offsets with a sample rate. Intervals are half-open.

### Claims

**S-16.** An archive MUST be able to hold conflicting claims about the same
evidence, each with its provenance, without its storage choosing between them.

**S-17.** An assessment MUST NOT erase the claim it judges or that claim's
provenance.

**S-18.** Which claim is current MUST NOT be stored as evidence. It MUST be
defined only by a stated rule: the rule in the claim's contract, or a named
current rule that a reader asks for by name and version, which says what it
chose and why and reports a conflict rather than choosing. Every
materialisation of it MUST derive from that rule.

**S-19.** Evidentiary authority MUST NOT be assigned solely by whether the agent
was a person, a model, a deterministic process, or an external authority.

**S-20.** A correspondence between an archive subject and an external one MUST be
recorded as a mapping claim, never as an assertion that they are identical.

### Deletion and copies

**S-21.** Deletion is permitted; an archive is not required to be append-only.
Retiring an object MUST leave its records in place. Destroying content MUST be
refused while any unretired object refers to it, and MUST NOT implicitly destroy
dependent content: each dependent with its own content is decided explicitly.
Destroyed content MUST NOT count as a copy anywhere and MUST NOT be placed again.
Managed copies not yet deleted MUST be reported as pending, and known copies the
archive does not control MUST be reported as external survivors.

**S-22.** Every destroy transition MUST be initiated by a person and MUST record
at least the content digest, size, reason, and agent. Retirement and destruction
MUST NOT be carried out on the strength of policy alone; policy MAY recommend
them.

**S-23.** Consequential transitions MUST keep enough provenance to explain why
they happened. Where policy caused one, the record MUST identify the policy
revision, the rules that fired, and the facts they matched. The record MUST
identify the specification version in force.

**S-24.** Placement requirements MUST be counted in failure domains. Managed
copies count; an observed copy counts only when recently enough verified;
projections and published copies MUST NOT count.

**S-25.** A reclaim verdict that the operator acts on MUST be recorded as
evidence, with the survivors, their failure domains, and the verification state
that justified it.

### Policy and plans

**S-26.** Policy MUST be versioned, and changes to it MUST pass through Door.
Authoritative policy MUST be authorized by the operator. A policy revision MUST
record its author and its authorizer as separate facts; a machine MAY author a
proposal but MUST NOT enact it, and authorization MUST NOT record a machine's
authorship as a person's.

**S-27.** Policy MUST be written in the policy language this specification
defines. Rules MUST only add desired state; no rule may cancel or lower another.
Policy MUST select only on evidence in the archive. A fact that cannot be
established MUST NOT cause a reduction in copies, a retirement, or a withdrawal;
a missing observation is unknown, not zero.

**S-28.** A plan MUST record the policy revision and evidence it was computed
from, and applying it MUST refuse any action whose conditions no longer hold.

### Publication and disclosure

**S-29.** Publication and withdrawal are transitions. A publication MUST depend on
an approval by a person, scoped to its destination (or to an exposure class the
operator names) and pinned to exact revisions. Every destination MUST have an
exposure class declared by policy; an undeclared destination MUST be treated as
distribution.

**S-30.** Publishing work that is not the operator's own beyond the operator's
circle MUST require an approval given for that publication. A change in rights
MUST NOT by itself cause publication. Open and distribution destinations MUST
require a rights claim permitting publication; unknown or restrictive rights MUST
block it. The circle is private: rights MUST NOT limit what may be disclosed to
it, and only sensitivity does. A Work the operator wrote with others MUST NOT be
disclosed beyond its band without each other writer's recorded consent for that
destination and those exact revisions; inside the band the operator's approval
suffices. Door MUST refuse an approval of publication beyond the circle
that the archive's own sensitivity, rights, or consent claims forbid on the
day it is made, and a publisher MUST check them again when it publishes.

**S-31.** A withdrawal from an irreversible destination MUST be recorded as
requested, with known outside copies reported.

**S-32.** Content MUST NOT be disclosed except to a destination that policy
grants for it; a fact that cannot be established MUST NOT widen disclosure.
Every disclosure MUST be recorded with its destination, the content, and the
policy revision that allowed it, whether or not any result is kept, and MUST
be recorded before any content is sent; a send that may have begun counts as
received. A processor
that received content MUST be reported as a possible external survivor when
that content is destroyed. Disclosing a record discloses what it pins,
transitively, and a cap on anything it pins is a cap on it. A disclosure to the
circle is a publication approval to the circle's destination, recorded before
any member reads what it covers. A sensitivity claim MUST cap disclosure: RED at
private, AMBER at circle, GREEN at service; CLEAR sets no cap. When an object
carries several sensitivity claims, the most restrictive MUST apply. A
sensitivity claim whose source is a person the content records MUST NOT be
withdrawn except by a later claim from that person. The operator MAY disclose
past such a claim only to one named destination no further out than service,
with a recorded reason; content MUST NOT be published past it to open or
distribution. Disclosure compelled by law
MUST be recorded like any other.

## 3. Conventions

P-1 through P-7 and P-9 through P-12 are made normative by the corresponding
requirements in section 4. P-8 and P-13 through P-15 remain provisional while
their open questions are unresolved.

- **P-1.** Records are JSON files committed to Git, one file per revision.
  Content is managed by git-annex.
- **P-2.** A record's envelope has the fields `spec`, `revision_id`,
  `object_id`, `shape`, `by`, `agent`, `at`, `payload`, `refs`, `supersedes`,
  and optionally `source` and `contracts`. No other envelope field is allowed.
- **P-3.** `revision_id` is derived from the SHA-256 of the record's RFC 8785
  (JCS) canonical form with `revision_id` and `at` removed, so an identical
  retry yields the identical revision. `spec` stays in: the same payload under
  another specification version is another revision.
- **P-4.** A reference is `{role, id, revision_id?}`; `revision_id`, when
  present, pins an exact revision.
- **P-5.** `at` is recording time, never capture time. Door stamps it at
  commit; a retry of a stored revision keeps the first `at`. An unknown capture time
  is recorded as unknown, never substituted.
- **P-6.** Content digests are SHA-256.
- **P-7.** `archive init` copies this document and its version into the new
  archive.
- **P-8.** The policy language is a subset of standard Datalog: rules, safe
  negation, and a maximum. Its grammar and test fixtures will be given here.
- **P-9.** An archive receives a UUID when it is created; object identifiers are
  formed from it and the `object_id` under a non-resolvable scheme.
- **P-10.** Content identifiers are equivalent to RFC 6920 `ni:` URIs.
- **P-11.** Claim-kind contracts are JSON Schema documents with a JSON-LD context
  fragment; controlled vocabularies are SKOS concept schemes.
- **P-12.** Agents that are people or organisations are entities in the archive;
  software and models are identified by Package URL where one exists.
- **P-13.** A PREMIS Level 1 mapping is stored alongside this specification.
- **P-14.** Entity kinds follow the IFLA Library Reference Model, with
  MusicBrainz kinds for music mapped onto it.
- **P-15.** Sensitivity claims use the Traffic Light Protocol 2.0 labels.

## 4. Format and protocols

### D-1. On-disk layout (ADR 0020)

An archive root MUST contain these paths:

- `archive.json`;
- `spec/<version>/VERSION`;
- `spec/<version>/archive-spec.md`;
- `spec/<version>/context.jsonld`;
- `contracts/sha256-<digest>.json`;
- `records/<h0h1>/<h2h3>/sha256-<digest>.json`; and
- `content/sha256/<h0h1>/<h2h3>/<digest>` for each locally present content
  item.

`<h0h1>` and `<h2h3>` are the first and second pairs of characters of the
lowercase hexadecimal digest in the same path. This is layout `archive/2`
(ADR 0053); specifications before 0.0.6 used layout `archive/1`, with every
record directly in `records/` and every content path directly in
`content/sha256/`. Specification 0.0.6 changes only the layout: it installs
the same contract bundles and the same core context as 0.0.5. Specification
0.0.7 keeps layout `archive/2` and the 0.0.6 core context and adds contract
bundles (D-15), specification 0.0.8 keeps them again and adds one more
(D-16), and specification 0.0.9 keeps layout `archive/2`, adds two Entity
kinds to the core context, and adds five bundles (D-17).

`archive.json` MUST contain only the layout version, archive identifier, and
governing specification identifier. Its layout MUST be `archive/2`. The specification directory and contract
files MUST contain the exact local documents needed to interpret admitted
records. A contract filename MUST equal the lowercase SHA-256 digest of its
contents. A record filename MUST equal the lowercase SHA-256 part of the
record's `revision_id`.

`content/` MUST be a git-annex worktree. Its paths are locations, not object
identities. Object identifiers and source names MUST NOT determine content
paths. Locks, proposals, plans, indexes, views, caches, and other operational
or regenerable state MUST NOT be required to recover archival evidence.

Changing the governing specification of an existing archive is possible only
by the specification upgrade transition of D-15, and only to an additive
release; every other change of specification remains unsupported.

### D-2. Portable identifiers (ADR 0021)

An archive identifier MUST have the form `urn:uuid:<archive-uuid>`, with a
canonical lowercase RFC 9562 UUID created when the archive is initialized.

An object identifier MUST have the form `urn:uuid:<object-uuid>`. Door MUST
derive `object-uuid` using RFC 9562 UUIDv5 with the archive UUID as namespace
and the UTF-8 sequence `identity namespace`, NUL, `identity name` as input. The
identity name MUST be a non-empty ASCII token containing only letters, digits,
hyphen, and underscore. It MUST NOT be derived from a path, filename, title,
location, or content digest. Records and references MUST store the full object
URN.

A content digest MUST be written as `sha256:` followed by 64 lowercase
hexadecimal digits. Its RFC 6920 equivalent is the `ni:///sha-256;...` URI made
from the same digest bytes using unpadded base64url. Records MUST store only the
`sha256:` spelling.

### D-3. Normative record envelope (ADR 0022)

A stored record MUST be a JSON object with exactly these required fields:
`spec`, `revision_id`, `object_id`, `shape`, `by`, `agent`, `at`, `payload`,
`refs`, and `supersedes`. It MAY contain `source` and `contracts`. It MUST NOT
contain another top-level field.

`spec` MUST have the form `archive/<major>.<minor>.<patch>`. `revision_id` MUST
be a canonical SHA-256 digest. `object_id` MUST be a portable object identifier.
`shape` MUST be exactly `artifact`, `entity`, `claim`, or `assessment`. `by`
MUST identify the writing program and `agent` MUST separately identify the
asserting person, organisation, model, or process; an organisation asserts
only where its contract allows it, such as a band's adopted lead sheet (D-18). `at` MUST be Door's RFC 3339 UTC recording
time. `payload` MUST be a JSON object.

`refs` and `supersedes` MUST be arrays even when empty. A reference MUST contain
only non-empty `role` and `id` strings and an optional canonical
`revision_id`. Its `id` MUST be an object identifier. A supersession reference
MUST have role `supersedes`, MUST pin an exact revision, and MUST name the same
object as the record.

`source`, when present, MUST contain only the non-empty strings `schema` and
`id` and an optional non-empty `revision_id`. It records imported source
identity and MUST NOT supply Archive object identity. `schema` MUST be a
portable identifier, not a repository path.

`contracts`, when present, MUST be a non-empty array. Each entry MUST contain
only non-empty `id` and `version` strings and a canonical SHA-256 `digest`.
Every claim MUST cite the contract that defines its payload. Another shape MUST
cite a contract when its payload depends on one. An empty `contracts` array
MUST be refused.

An admission proposal MUST omit `revision_id` and `at`; Door supplies them.
The proposal MUST include `refs` and `supersedes`, including when they are
empty. Retrying the same proposal MUST return the stored revision with its
original recording time. Envelope and payload validation MUST reject unknown
fields rather than discard them.

### D-4. Admission and staging (ADR 0023)

An admission proposal MUST be a metadata-only directory containing
`manifest.json` and the record drafts it names beneath `records/`. Its closed
manifest MUST identify protocol `archive-admission/1` and contain ordered
`records`, `identities`, `contents`, and `expected_heads` arrays, including when
empty. `identities` MUST contain exactly one allocation defined by D-7 for each
new object and none for an existing object.
Proposal paths MUST be normalized relative paths to distinct regular files
beneath the proposal root and MUST NOT contain an empty, `.` or `..` component
or traverse a symlink.

Each content declaration MUST contain one canonical digest, byte size,
git-annex key, and configured staging-remote identifier. Every content item
referenced by a proposed artifact MUST be declared exactly once, and every
declaration MUST be referenced. `expected_heads` MUST contain exactly one row
for every object created or revised; its expected revision is null for a new
object and an exact pin for an existing object.

Door MUST validate the complete proposal under one archive lock before writing
a record. It MUST use git-annex to obtain each key from the configured staging
remote, require the key to be present locally, verify local fixity, and compare
both digest and byte size independently with the manifest. A key name, remote
claim, or location log alone MUST NOT count as verification. Content bytes
MUST NOT pass through Door's input, output, or memory.

After validation Door MUST recheck the retained proposal bytes and archive
heads, compute all revisions, create the canonical content and record paths,
stage only those paths, and commit the batch once. A failure MUST leave no
authoritative record or partial commit. Already transferred content remains
reported, unadmitted storage until retried or explicitly cleaned up.

Before checking the proposal's original expected heads, Door MUST detect an
identical previously committed complete batch and return its stored revisions
without a record, commit, or location write. A partially present revision set,
or a stale head when no complete retry exists, MUST be refused. A dry run MAY
validate metadata and predict revisions, but MUST NOT transfer content or
report fixity as verified.

Retry identity is the complete set of computed record revisions introduced by
one commit. Proposal order controls response order, but is not archival
identity. Expected heads and the staging transport fields `annex_key` and
`staging_remote` MUST still validate, but are operational inputs that are not
retained and do not change retry identity; the artifact's retained digest and
size do.

`door admit --proposal <directory> --dry-run` MUST run from the archive root.
On success it exits 0 and writes one closed JSON object to stdout containing
exactly `status` equal to `predicted`, ordered `revision_ids`, ordered canonical
`records`, `content_verified` equal to false, and `retry`. It writes nothing to
stderr. A refusal exits nonzero, writes no stdout, and explains the refusal on
stderr. The dry run MUST read archival evidence from one committed Git snapshot
and recheck the retained proposal bytes before reporting success. It MUST NOT
transfer content, invoke git-annex, change the archive or proposal, or claim
content fixity.

Without `--dry-run`, a proposal with no content declarations and no artifact
records MAY be admitted without invoking git-annex. Door MUST hold the archive
lock across validation, retained-byte and snapshot recheck, and the write;
refuse a staged ordinary Git index; create every canonical record and advance
the current branch in one commit; and leave unrelated working-tree files and
changes untouched. On success it exits 0 and writes one closed JSON object
containing exactly `status` equal to `committed`, ordered `revision_ids`, and
`retry`. An identical complete retry, including a previously committed
content-bearing batch, MUST return without invoking git-annex, changing bytes,
or creating a commit. Until the required INV-5 git-annex experiment has been
run and its result accepted, every new content-bearing or mixed proposal MUST
fail loudly as unsupported before mutation or git-annex invocation. A refusal
exits nonzero, writes no stdout, and explains the refusal on stderr.

Git-annex is responsible for transfer, byte storage, presence, fixity
operations, and location logs. Door is responsible for proposal validation,
the verified result, archive invariants, expected heads, record identity, and
the commit that makes the admission authoritative.

### D-5. Retire and destroy transitions (ADR 0024)

The specification MUST ship version 1 claim contracts named
`archive.transition/retire` and `archive.transition/destroy`. A claim under
either contract MUST have one pinned `author` reference to a person entity and
one pinned `authorizer` reference to the active operator's person entity. The
same person MAY fill both roles, but the facts remain separate. `agent` MUST
identify the author.

A retire claim MUST have the closed payload `{reason}` and exactly one
`retires` reference pinned to the current revision of the object leaving the
collection. Recording it MUST retire the object without removing its records.

A destroy claim MUST have the closed payload
`{content, reason, external_survivors}`, where `content` contains exactly a
canonical digest and non-negative byte size and `external_survivors` is an
array, including when empty. It MUST have one or more pinned `destroys`
references to retired artifact revisions carrying those bytes. Door MUST
refuse it while an unretired object refers to the content.

Every dependent artifact with content of its own that is reachable from the
destroyed content under the installed contracts MUST have exactly one pinned
reference with role `dependent_destroy` or `dependent_source_gone`. A
`dependent_destroy` reference MUST correspond to a destroy claim for that
dependent content in the same atomic batch. Claims and assessments without
bytes remain as history and require no disposition.

Every known outside copy MUST have one closed `external_survivors` row with
non-empty `destination`, `basis` equal to `disclosure` or `verified_copy`, and
an RFC 3339 `observed_at`. When archival evidence exists, a pinned
`external_survivor` reference MUST identify it. An outside survivor MUST NOT
block destroy or deletion completion.

Recording the destroy claim MUST immediately make the content destroyed for
archival purposes. The claim MUST NOT store managed locations, deletion
attempts, or pending/completed execution state. Those are derived from the
claim and current storage. Later survivor evidence MUST be a new record, never
an edit of the destroy claim. Door and apply MUST refuse every later attempt to
place content whose destroy claim is authoritative.

When policy contributes to a transition, the claim MUST also carry the policy
provenance required by S-23. Policy-caused retire and destroy MUST remain
unsupported until the policy grammar, stable rule names, and matched-fact
representation are defined. Information erasure and expungement remain
unsupported.

### D-6. JSON-LD context versioning (ADR 0025)

Every specification version MUST include exactly one immutable core JSON-LD
context at `spec/<version>/context.jsonld`. A record's `spec` value MUST select
that context. A record MUST NOT contain an `@context` field, and a conforming
reader MUST NOT fetch a context or vocabulary from the network.

The core context MUST define the meaning of core envelope, shape, reference,
and provenance fields. Contract fragments MUST define their payload and
relation-role terms, including transition terms. A change to any core mapping
requires a new specification version. Existing records MUST retain the context
selected by their original `spec` value.

A contract MUST be one closed JSON object containing exactly `id`, `version`,
`schema`, and `context`. `schema` MUST be the JSON Schema for the
contract-defined payload and relations. `context` MUST map only terms owned by
that contract and MUST NOT redefine a core term or a term owned by another
cited contract. It MUST be an object of term definitions and MUST NOT contain
`@context`, `@import`, or a term definition that selects a remote context.

The contract digest MUST be SHA-256 over the RFC 8785 canonical form of the
whole bundle. Its contract file MUST contain exactly those canonical bytes, and
its filename and every citation MUST match that digest. The citation's `id`
and `version` MUST also match the bundle. Any schema or context change requires
a new contract version and digest; the same version MUST NOT identify different
bytes.

Door MUST validate envelopes and contract JSON Schemas without JSON-LD
processing. JSON-LD supplies interpretation, not admission semantics. A reader
missing the selected core context or a cited bundle MUST report an incomplete
read. For interpretation, a reader MUST start with the object inside the core
context's top-level `@context` and then consider contract fragments in citation
order. A term collision between any two objects MUST be refused on admission
and reported as an incomplete read. An imported vocabulary term MUST be
constrained by an Archive profile or used only as a mapping; a context mapping
MUST NOT import weaker semantics.

### D-7. Initialization and identity allocation (ADR 0026)

`archive init --operator <identity-name> <root>` MUST create an archive only
when the root does not exist or is empty. It MUST generate a random RFC 9562
UUIDv4 and write a closed, RFC 8785-canonical `archive.json` containing exactly
`archive_id`, `layout`, and `spec`, with values `urn:uuid:<archive-uuid>`,
`archive/2`, and the governing `archive/<version>`.

Initialization MUST create the normative layout, initialize Git and git-annex,
copy the governing specification, core context, and specification-owned
contract bundles, and make one initial commit. That commit MUST contain a
person entity and an `archive.transition/operator_initialization` version `1`
claim with pinned `author`, `authorizer`, and `appoints` references to that
person revision. It is the only transition permitted without a previous active
operator. The active operator MUST be derived from transition claims, not a
mutable manifest field. Succession MUST remain unsupported until its
verification is defined.

The operator entity MUST use identity namespace `archive.core/entity` and the
supplied operator identity name. The initialization claim MUST use namespace
`archive.transition/operator_initialization` and name `initial`. Core artifact
objects use `archive.core/artifact`; other core entities use
`archive.core/entity`; contract-owned objects use their contract identifier as
namespace.

An identical init retry MUST return the stored archive and operator identifiers
without writing. A non-empty uninitialized root, changed manifest, incomplete
bootstrap, or different requested operator MUST be refused without cleanup of
pre-existing paths.

Every new object in an admission proposal MUST have exactly one row in a
required `identities` array; existing objects MUST have none. A row contains
exactly `object_id`, `namespace`, and `name`. Door MUST derive and compare the
object identifier by D-2. The namespace MUST use lowercase slash-separated
segments, each beginning with a letter and continuing with letters, digits,
dot, underscore, or hyphen. Specification-owned namespaces begin `archive.`.
Contract identifiers and `source.schema` use the same syntax.

`by` MUST be a canonical Package URL. `agent` MUST be an Archive object URN for
a person, organisation, software, model, or process entity, or a canonical
Package URL for packaged software, a model, or a process. Such a Package URL
MUST NOT have a qualifier, subpath, URI authority, repository URL, download
URL, or component parsed as a DNS hostname. An unpackageable agent MUST first
be represented by an Archive entity. None of these identifiers may contain a
hostname.

### D-8. Core payloads and installed contracts (ADR 0027)

An artifact payload MUST be the closed object `{content}`, where `content`
contains exactly a canonical SHA-256 digest and a non-negative byte size.
Location, annex key, filename, source path, capture time, media type, origin,
and preference MUST NOT be artifact payload fields.

An entity payload MUST be the closed object `{kind, label?}`. `kind` MUST be a
non-empty term defined by the core context or an installed controlled
vocabulary. `label`, when present, MUST be a non-empty string and MUST NOT
supply identity. The core context MUST define `person`, `organisation`,
`software`, `model`, and `process`. Specification 0.0.3 also defines
`musical_work` in its versioned context. A context for 0.0.1 or 0.0.2 MUST NOT
accept that later kind. Specification 0.0.5 also defines `recording_session`,
a local kind with no Library Reference Model equivalent; a context for an
earlier version MUST NOT accept it. Specifications 0.0.6, 0.0.7, and 0.0.8
keep the 0.0.5 kinds and core context unchanged. Specification 0.0.9 also
defines `release` (one edition of music as issued, after MusicBrainz's
Release) and `recording` (after MusicBrainz's Recording), mapped to
`arch:Release` and `arch:Recording`; a context for an earlier version MUST NOT
accept them, and their identities are opaque, never a title or an external
identifier. Specification 0.0.10 keeps the 0.0.9 kinds and core context
unchanged. This Archive-owned term does not establish an RDF
equivalence between Archive and MusicBrainz Work types or settle the open LRM
mapping question.

A claim MUST cite at least one contract. An assessment MUST cite at least one
contract and MUST have one or more exact `assessment_of` references to claim
revisions. Every cited contract schema MUST validate the closed payload and
the reference roles it owns. The payload MUST NOT repeat a kind or contract
version.

The specification distribution MUST contain canonical bundles for version `1`
of `archive.transition/operator_initialization`,
`archive.transition/retire`, and `archive.transition/destroy`. Their schemas
MUST use JSON Schema 2020-12, be closed and self-contained, contain no remote
`$ref`, and encode D-5 and D-7. Their context fragments MUST follow D-6. This
specification release MUST leave those version `1` bundles unchanged and add
version `2` retire and destroy bundles whose
schemas permit `transition_predecessor` and whose context fragments map it
exactly to `arch:transition_predecessor`. The version `1` initialization bundle
MUST NOT permit that role.

Initialization MUST verify and copy every specification-owned bundle before
creating a record that cites it. Door MUST resolve a citation by its digest
path, verify the bundle bytes, and then require its identifier and version to
match. Missing, changed, unknown, or remotely dependent contracts MUST be
refused on admission and reported as incomplete on read.

Adding contracts outside a specification release, domain assessment
contracts, enrichment contracts other than those of D-15 to D-18, and current rules
remain unsupported until separately defined.

### D-9. Transition proposals and dispatch (ADR 0028)

`door transition --proposal <directory>` MUST accept a metadata proposal with
the safe-path and retained-byte rules of D-4. Its closed manifest MUST contain
protocol `archive-transition/1` and non-empty ordered `records`, `identities`,
and `expected_heads` arrays. It MUST contain exactly one identity allocation
for every new transition object and an expected head for every existing object
whose revision or derived state the transition relies on, plus a null
expectation for every new transition object.

Door MUST verify each contract bundle and dispatch a transition by the exact
tuple of contract identifier, version, and digest. The initial table contains
only the installed `operator_initialization`, `retire`, and `destroy` bundles.
Only initialization may invoke `operator_initialization`; `door transition`
supports retire and destroy. Every other transition MUST fail as unsupported.
Generic admission MUST refuse a claim citing a transition contract.

Under one archive lock Door MUST validate the complete proposal, identities,
contracts, references, expected heads, author, authorizer, active operator, and
legal pre-state; compute every revision and resulting state; recheck the
retained bytes and heads; then write all records in one Git commit. Every
`dependent_destroy` artifact MUST have exactly one matching destroy draft in
the batch. References to an existing or earlier computable revision MAY be
used; a forward reference or dependency cycle MUST be refused.

The transition order that affects authorization and pre-state MUST survive
the proposal directory. In a multi-record transition proposal, the first
record MUST have no `transition_predecessor` reference and every later record
MUST have exactly one pinned `transition_predecessor` reference to the
immediately preceding transition draft in that same proposal. The reference
MUST name a transition claim and MUST resolve to an earlier draft, not to a
record from another committed batch. Door MUST require the manifest's record
order and this predecessor chain to agree, and MUST refuse missing,
duplicated, branching, cyclic, cross-batch, or out-of-order chains. A
single-record proposal has no predecessor. The chain is committed in the
records and is the durable order for replay; it is not an operational sidecar.
Every record in a multi-record chain MUST cite version `2` of its retire or
destroy contract. Version `1` retire and destroy contracts remain valid only
for single-record transition proposals. A complete retry MUST satisfy D-9's
existing complete-batch rule: the same computed revisions, including their
exact predecessor pins, MUST all have been introduced by one committed Git
batch. Chain equality MUST NOT weaken any non-retry identity, authorization,
expected-head, or state check.

Before checking the proposal's original expected heads, Door MUST detect an
identical previously committed complete batch and return its revisions in
manifest order without writing. A partially present batch, or—when no complete
retry exists—a changed contract, authorization, expected head, or state
precondition MUST be refused. A dry run MAY perform pure validation and identity
computation but MUST report that authorization and state are rechecked at
commit.

Before validating a new transition or recognizing a retry, Door MUST replay
the committed record history from the archive root to the pinned snapshot.
It MUST establish each record's introducing commit, refuse a normative
manifest, specification, contract, or record path that was later modified,
deleted, renamed, copied, or re-added, and refuse a shallow or otherwise
incomplete history whose root-to-snapshot order it cannot prove. Such a
refusal reports incomplete evidence; it does not reinterpret the visible
subset as a complete archive.

Replay MUST validate the root initialization before establishing the active
operator, reconstruct object heads from explicit supersession, and validate
every historical transition against the actors, current heads, and legal
pre-state that preceded it. A commit containing retire or destroy records MUST
contain only one transition batch. Door MUST recover a multi-record batch's
order solely from its durable `transition_predecessor` chain and MUST reject a
chain that does not cover exactly the transition records introduced by that
commit. Each effect is applied in that order. A record introduced by a later
commit MUST NOT satisfy an earlier reference, and an artifact record introduced
after destruction MUST NOT place that destroyed content back into archival
custody.

`door transition --proposal <directory> --dry-run` MUST run from the archive
root. On success it exits 0 and writes one closed JSON object to stdout
containing exactly `status` equal to `predicted`, ordered `revision_ids`,
`retired_objects`, `destroyed_digests`, `authorization_recheck_required` equal
to true, `state_recheck_required` equal to true, and `retry`. It writes nothing
to stderr. A refusal exits nonzero, writes no stdout, and explains the refusal
on stderr. The command MUST use one committed Git snapshot, recheck retained
proposal bytes, and MUST NOT write or invoke git-annex.

Without `--dry-run`, the command MUST hold the archive lock across validation,
the retained-byte and snapshot recheck, and the write. A new batch MUST create
all canonical record files and advance the current branch in one Git commit;
no successful prefix may remain after refusal. An identical complete retry
MUST create no commit and MUST preserve the original stored bytes and `at`.
On success the command exits 0 and writes one closed JSON object containing
exactly `status` equal to `committed`, ordered `revision_ids`,
`retired_objects`, `destroyed_digests`, and `retry`. A refusal exits nonzero,
writes no stdout, and explains the refusal on stderr. Persistent transition
MUST NOT invoke git-annex and MUST leave unrelated working-tree files and
changes outside its record batch untouched.
Door MUST refuse a persistent transition while the ordinary Git index contains
staged changes, so no bypass write can be mistaken for part of Door's batch.

Policy-caused and all other transition kinds MUST remain unsupported until
their provenance representation, contracts, and handlers are accepted.
Because Archive 0.0.2 installs no artifact relation or survivor-evidence
contracts, a destroy proposal containing `dependent_destroy`,
`dependent_source_gone`, or `external_survivor` references MUST fail loudly as
unsupported; Door MUST NOT guess reachability or evidence kind from an
uncontracted reference.

## 5. Core context and bootstrap contract

### D-10. Core context and bootstrap contract (ADR 0029)

The Archive-owned vocabulary namespace is
`urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#`. The core context MUST define
the prefixes `arch`, `as`, `dcterms`, `prov`, `rdf`, `rdfs`, and `xsd` with the
IRIs listed in ADR 0029. It MUST map `by` to `as:generator`, `agent` to
`dcterms:creator`, `at` to `prov:generatedAtTime`, `kind` to `rdf:type`, and
`label` to `rdfs:label`.

The values `person`, `organisation`, and `software` MUST map to `prov:Person`,
`prov:Organization`, and `prov:SoftwareAgent`. `model` and `process` MUST map
to `arch:ModelAgent` and `arch:ProcessAgent`, which are Archive profiles of
`prov:SoftwareAgent`. Every other core field and controlled value MUST map
one-for-one to its `arch:` name. `role` MUST be vocabulary-valued; installed
contract fragments define allowed role terms. Generic `id` and `revision_id`
MUST NOT be JSON-LD `@id` aliases.

The version `1` `archive.transition/operator_initialization` contract MUST
accept only the empty payload `{}`. It MUST require exactly one pinned
`author`, `authorizer`, and `appoints` reference, all naming the same person
revision, and `agent` MUST name that person. Its context maps those roles to
`arch:author`, `arch:authorizer`, and `arch:appoints` under Archive's PROV
profile.

Archive version 0.0.2 MUST write `pkg:hackage/archive@0.0.1.0` as `by`.

This provision corrects ADR 0012's nonexistent `oa:generator` and
`oa:creator` mappings to the Web Annotation terms `as:generator` and
`dcterms:creator`.

## 6. Reading commands

The reading commands run from the current working directory, which MUST be the
archive root. They MUST obtain `archive.json`, the selected core context, and
records from committed Git `HEAD`; worktree files MUST NOT affect a read.
Their stdout is one closed JSON object, and they MUST emit no other stdout.

Every successful response contains `{"status":"ok"}`. An incomplete response
contains `{"status":"incomplete","issues":[...]}` and exits 2; it MUST retain
all usable records it read. A failed Git acquisition contains
`{"status":"failed","issues":[...]}` and exits 1. Each read issue contains
exactly `kind`, `path`, and `detail`; `kind` is one of `git`, `missing_tree`,
`parse`, or `unsupported`, and `path` is either a relative archive path or
null. A structural Doctor issue instead contains exactly `code`,
`revision_id`, `object_id`, and `detail`; either identifier may be null. `code`
is one of `duplicate_revision`, `duplicate_object_revision`,
`dangling_reference`, `wrong_revision_pin`, `unpinned_supersession`,
`invalid_supersession_role`, `supersession_wrong_object`,
`supersession_wrong_shape`, `supersession_fork`, `supersession_cycle`,
`missing_assessment_target`, `unpinned_assessment_target`,
`assessment_target_wrong_shape`, `missing_contract_citation`,
`invalid_contract_citation`, or `contract_not_installed`.

For `status`, `doctor`, `ls`, `show`, and `current`, all command output is on
stdout as the one JSON object described here; these commands write nothing to
stderr. Exit 0 means the requested projection completed, exit 1 means Git
acquisition failed (or `doctor` found structural issues), and exit 2 means the
projection is incomplete or deliberately unsupported. Usage errors remain
command-line errors and may use stderr; `archive init` retains its separate
initialization interface.

In addition to `status`, `archive status` returns exactly `archive_id`, `spec`,
`context_present`, and `record_count`. `archive ls` returns those fields and
`records`, an array of closed objects containing exactly `revision_id`,
`object_id`, `shape`, and `path`. `archive show <object-or-revision>` returns
the status fields plus `selector` and `records`; the latter
contains the raw stored record values that match the selector. After a complete
read, no match is the complete empty answer, not an incomplete read. `archive
doctor` returns the status fields plus `issues`, `doctor_issue_count`,
`coverage`, `contracts`, and `current`; its coverage and check objects are
closed and explain checks which were not run. An incomplete response adds
`issues` to the normal command fields. A failed acquisition contains only
`status` and `issues`. `current` is deliberately unsupported until each contract
defines a current rule: it MUST print exactly the error string
`unsupported: no current rule is defined` in a closed object containing exactly
`status` equal to `unsupported` and that `error` field, then exit 2.

These commands are projections only. They MUST NOT write the archive, create a
cache, choose a current claim, or treat unreadable evidence as absent (INV-4,
INV-7, INV-18, INV-24).

### D-11. Musical works and performance correspondence (ADR 0042)

Specification 0.0.3 installs the entity kind `musical_work` and two closed
claim contracts, each version 1:

- `archive.claim/performance_work` has an empty payload and exactly two
  references: `performance` and `work`. Both MUST pin an exact revision. The
  `performance` reference MUST target a Claim revision; the `work` reference
  MUST target an Entity revision with kind `musical_work`. This is a
  structural check and does not establish the meaning or truth of the target
  performance claim. A reader MUST preserve the claim and its provenance, and
  MUST NOT treat the target as a performance unless its own contract supports
  that interpretation.
- `archive.claim/work_musicbrainz_correspondence` has a closed payload with
  `external_work`, a canonical `https://musicbrainz.org/work/<MBID>` URI, and
  `mapping`, either `skos:exactMatch` or `skos:closeMatch`. Its one `work`
  reference MUST pin an exact `musical_work` Entity revision.

Each claim has an opaque object ID supplied by its proposer, independent of
the work IDs and external URI. These claims do not define `current`; readers
show competing claims and assessments without selecting a winner. World may
map SKOS catalogue concepts for the local and MusicBrainz works, but MUST NOT
assert `owl:sameAs` or merge their Archive object identities. Neither contract
settles the current rule, admits external source content, or defines a general
MusicBrainz collection policy.

These contract bundles use the root schema annotation
`x-archive-reference-targets`, which maps a reference role to a closed target
rule containing `shape` (`claim` or `entity`) and an optional entity `kind`.
Door checks the rule against the exact committed revision named by the
reference. This annotation is part of Archive's closed contract profile; it
does not add fields to records or make target claims true.

### D-12. Audio selections and song occurrences (ADRs 0044 and 0045)

Specification 0.0.4 installs version 1 claim contracts
`archive.claim/audio_selection` and `archive.claim/song_occurrence`, and
version 2 of `archive.claim/performance_work`. Every older contract bundle
remains installed with its exact earlier bytes.

An audio selection MUST have a nonempty ordered `pieces` payload. Each piece
MUST contain exactly integer `start_sample`, `end_sample`, and `sample_rate`.
The start MUST be nonnegative, the end MUST exceed the start, and the sample
rate MUST be positive. The Claim MUST have exactly one `piece` reference per
payload piece, in the same array position. Every such reference MUST pin an
exact committed Artifact revision. A selection MAY repeat an Artifact or
cross Artifact boundaries. Door does not claim that the bounds fit the audio
bytes without separate admitted duration evidence. A correction to the same
intended excerpt MUST supersede its previous selection revision; a different
excerpt MUST have another object identity.

A song occurrence MUST have an empty payload and one or more exact `selection`
references to audio selection Claim revisions. These references MUST be
unique and sorted by object ID and revision ID because membership has no
semantic order. Two concurrent capture perspectives of one rendition MAY
belong to one occurrence; separate takes MUST have separate occurrence
identities. An occurrence revision MUST change explicitly when its selection
pins change. A single selection MUST NOT produce an occurrence merely because
it has a title; the operator must assert a rendition or link it to a durable
Work.

`archive.claim/performance_work` version 2 retains the version 1 payload and
reference roles but requires its `performance` reference to pin a Claim citing
the song-occurrence version 1 contract. Version 1 remains a structural Claim
target check and MUST NOT be read as proof of a song occurrence. World MUST
preserve the exact Claim revisions, their attribution, and disagreement; it
MUST NOT choose current relations without per-kind current rules.

The contract profile extends `x-archive-reference-targets` with Artifact
shape, repeated roles (`many: true`), and a Claim target's exact `contract`
identifier and version. `x-archive-ordered-sample-spans` names the payload
array, reference role, and sample-bound fields Door checks in index order.
`x-archive-unique-sorted-refs` names a repeated role Door checks for unique,
canonical membership. These root-only rules are closed; unknown or malformed
rules MUST be refused, and generic JSON Schema success alone MUST NOT bypass
them. This section defines no PWA gesture, annotation outcome, or `current`
rule.

### D-13. Review application contracts (ADR 0048)

Specification 0.0.5 installs version 2 of `archive.claim/audio_selection` and
version 1 of `archive.claim/capture_alignment`, `archive.claim/selection_kind`,
`archive.claim/selection_title`, `archive.claim/artist_credit`,
`archive.assessment/judgment`, `archive.transition/publication_approval`,
`archive.transition/publication_withdrawal`, and `archive.claim/session_parts`,
with version 2 of `archive.claim/song_occurrence` and version 3 of
`archive.claim/performance_work`. Every older contract bundle remains
installed with its exact earlier bytes, and an audio selection version 1
remains valid as a single-track selection.

**Occurrences of multi-source selections.** A song occurrence version 2 is a
song occurrence version 1 whose `selection` references MUST pin
`archive.claim/audio_selection` version 2 Claim revisions. A
`performance_work` version 3 is version 2 whose `performance` reference MUST
pin a song occurrence version 2 Claim revision. The earlier versions keep
their own targets: an occurrence version 1 groups version 1 selections, and
`performance_work` version 2 links an occurrence version 1.

**Multi-source selections.** A version 2 audio selection MUST have a nonempty
`tracks` payload. Each track MUST have a nonempty ordered `pieces` array of
spans with exactly integer `start_sample`, `end_sample`, and `sample_rate`,
where `0 <= start_sample < end_sample` and the rate is positive. All tracks
denote the same moment captured by different sources. The Claim MUST have
exactly one `piece` reference per span, in track-then-span order, and each
MUST pin an exact Artifact revision. Within one track, a span that pins the
same Artifact object as the span before it MUST start at or after that span's
end. No content digest MAY appear in two tracks of one selection: bit-identical
recordings are one source. Optional `alignment` references MUST pin
`archive.claim/capture_alignment` version 1 Claim revisions that the selection
relied on.

**Clock alignment.** A capture alignment MUST pin exactly one `reference` and
one `aligned` Artifact revision of different Artifact objects, and MAY pin one
`run` Claim revision. Its payload MUST contain exactly `reference_rate` and
`aligned_rate` (positive integer nominal sample rates), a nonempty `anchors`
array, a nonnegative integer `max_error_samples`, and a `method` with a
non-empty `id` (at most 200 characters) and `version` (at most 100). Each
anchor pairs a `reference_sample` with the `aligned_sample` recorded at the
same instant; anchors MUST strictly increase in both. Between consecutive
anchors the mapping is linear. Beyond the outer anchors it extends with the
nearest segment's slope, or, with one anchor, at the nominal rate ratio. A
consumer that needs one sample rounds half to even. Competing alignments
coexist; none is current.

**Selection facts.** `selection_kind`, `selection_title`, and `artist_credit`
each MUST pin exactly one `archive.claim/audio_selection` version 2 Claim
revision in role `selection`. A kind is one of `original`, `cover`,
`improvisation`, `noodle`, `talking`, `transition`, `incomplete`, or
`unknown`. A title has 1 to 200 characters and never identifies a Work. An
artist credit has an empty payload and 1 to 12 `artist` references, each
pinning an Entity revision of kind `person` or `organisation`; their order is
the credit order, and no Entity object MAY appear twice.

**Judgments.** An `archive.assessment/judgment` is an assessment whose one
`assessment_of` reference pins an exact Claim revision of any contract. Its
payload has a `verdict` of `affirmed`, `rejected`, or `withdrawn`, and an
optional `reason` of 1 to 2,000 characters. It never rewrites what it judges.

**Publication.** A publication approval pins one `authorizer` person Entity
revision and one or more `covers` Claim revisions; its payload names one
`destination` (1 to 200 characters) and a `decision` of `approve` or
`hold_back`. A publication withdrawal pins one `authorizer` person Entity
revision and the one `publication_approval` version 1 Claim revision it
`withdraws`, with an optional `reason`. These are transition contracts:
`door transition` commits them under the manifest, lock, expected-head, and
single-commit rules of the earlier transitions, and `door admit` MUST refuse
them (ADR 0051). The `authorizer` MUST pin the current revision of the active
operator's appointed person Entity; the envelope `agent` may differ. Every
`covers` reference MUST pin its object's current head, and `expected_heads`
MUST name each covered object. A `hold_back` decision passes the same checks
and withdraws nothing. A withdrawal MUST pin an existing approval revision
that no earlier withdrawal names. Door records `destination` exactly and does
not check it against policy.

**Sessions and songs.** A rehearsal session is an Entity of kind
`recording_session`; its `label` is its name, and renaming it is a new
revision of that Entity. Its identity name is `session-` followed by the
lowercase hexadecimal SHA-256 of its first part's content, in namespace
`archive.audio/session`. `archive.claim/session_parts` has an empty payload,
exactly one `session` reference pinning a `recording_session` Entity revision,
and one or more `part` Artifact revisions in recording order, with no Artifact
object twice. A song first identified by a review application is an Entity of
kind `musical_work` whose identity name is `song-` followed by a lowercase
RFC 9562 UUIDv4, in namespace `archive.audio/song`. Door validates these
identities by the general rules of ADR 0026; the name forms are obligations on
the proposer.

**Profile rules.** A reference target MAY name `kinds`, a nonempty set of
Entity kinds, in place of `kind`; it MAY be repeated with `many`. A target
rule constrains the references present; how many references a role needs is
stated by the schema. `x-archive-track-sample-spans` names the track array,
its span array, the span reference role, and the sample-bound fields.
`x-archive-distinct-track-content` names the span reference role whose
Artifact content digests MUST be distinct across tracks.
`x-archive-monotonic-anchors` names an array and two integer fields that MUST
both strictly increase. `x-archive-distinct-refs` names roles whose references
MUST name different objects. `x-archive-ordered-distinct-refs` names a role
whose references keep their order and MUST name different objects. The JSON
Schema keyword `maxLength` counts Unicode code points. These rules are closed
and root-only like the earlier profile rules, and a generic schema success
MUST NOT bypass them. This section defines no `current` rule.

### D-14. V2 migration route (ADR 0049)

Specification 0.0.5 also installs version 1 of
`archive.migration/translation`, `archive.migration/selection_translation`,
and `archive.claim/work_match`. Translated V2 evidence uses the native claim
contracts of D-12 and D-13; a translation record carries its provenance.

A translation MUST pin one or more `translated` Claim revisions or
`translated_assessment` Assessment revisions, and exactly one
`migration_source` Artifact revision: the frozen migration bundle. Its
payload has a `rule` naming one of `v2-song-performance/1`,
`v2-performance-review/1`, `v2-performance-kind/1`, `v2-performance-title/1`,
`v2-performance-artists/1`, or `v2-operator-identification/1`; a closed
`locator` (`member`, `member_sha256`, `offset`, `length`, record `sha256`); a
`source` naming the V2 `schema`, `id`, and optional `revision_id`; and an
optional `part_position`, a positive integer. A selection translation MUST pin
exactly one `translated` audio selection version 2 Claim revision and one
`migration_source` Artifact revision. Its payload has rule
`v2-audio-segment-chain/1`, the same locator and source, the `chain_root` and
nonnegative `chain_position` of ADR 0047 rule 1, `source_seconds` with `start`
and `end` as exact decimal text of at most six decimal places, the sample-rate
`probe` (`tool`, `tool_version`, positive `sample_rate`), and
`boundary_rule` `round-half-even/1`. A translation and the records it pins are
proposed in one admission batch; a contract reference MAY pin an earlier
record of the same batch, as any reference may.

A work match MUST pin exactly one `occurrence` and one or more `corpus` song
occurrence version 2 Claim revisions, which MUST name different objects;
exactly one `musical_work` Entity revision as `work`; an optional
`parameters` Artifact revision; and exactly one `run` Claim revision. Its
payload has the `method` (`id`, `version`), the `score` as exact signed
decimal text, its `scale`, and a `direction` of `higher_is_closer` or
`lower_is_closer`. Version 1's `run` target is any Claim, so no proposer can
satisfy a version 1 match's provenance; D-15 defines the run contract and a
work match version 2 that pins it. Implementations MUST NOT read a match of
either version as identity or as current.

These bundles extend the contract profile with the reference target shape
`assessment`. The pattern subset Door evaluates is: anchored patterns of
literals, escaped metacharacters, ASCII character classes without negation,
groups with alternation, and the quantifiers `?`, `*`, `+`, `{n}`, and
`{n,m}`; any other syntax MUST be refused when the bundle is loaded.

### D-15. Derivation runs, audio facts, and upgrades (ADRs 0064 to 0068)

Specification 0.0.7 installs version 1 of `archive.claim/derivation_run`,
`archive.claim/audio_stream`, `archive.claim/span_suggestions`, and
`archive.transition/specification_upgrade`, and version 2 of
`archive.claim/work_match` and `archive.claim/capture_alignment`. Every older
contract bundle remains installed with its exact earlier bytes. The layout,
the entity kinds, and the core context are those of 0.0.6.

**Derivation runs.** A derivation run is a Claim describing one execution of
one method. Its payload MUST contain exactly `method`, with an `id` of 1 to 200
characters and a `version` of 1 to 100; `software`, an array of 0 to 32
objects of exactly `name` (1 to 200 characters) and `version` (1 to 100),
naming the programs and libraries whose behaviour the output depends on, in
the order the tool reports them; and `started_at` and `ended_at`, RFC 3339
instants in UTC with a `Z` suffix. `ended_at` MUST NOT be earlier than
`started_at`. A run MAY pin any number of `used` references, each to an
Artifact, Claim, or Entity revision the run read, and at most one
`parameters` reference to the Artifact revision holding the run's
configuration as canonical JSON. Presets, thresholds, and prompts MUST be in
that Artifact, never in a run or output payload. The envelope `agent` MUST be
the tool's Package URL. A run's identity name is `run-` followed by a
lowercase RFC 9562 UUIDv4, in namespace `archive.derivation/run`, so every run
is a new object. A claim a run made pins it in role `run`. A run and its
outputs MUST be proposed in one admission batch, and a run that keeps no
output MUST NOT be proposed. The `agent` form, the name form, and the batch
rule are obligations on the proposer; Door checks the payload, the instants,
and the reference targets.

**Audio stream facts.** An audio stream Claim states the facts of one audio
stream. It MUST pin exactly one `artifact` Artifact revision and exactly one
`run` derivation run version 1 Claim revision. Its payload MUST contain exactly
`stream_index`, the stream's zero-based index in its container; `codec`, 1 to
100 characters as the probing method reports it; `sample_rate`, a positive
integer in hertz; `channel_count`, an integer from 1 to 64; `sample_count`, a
positive integer, the exact number of sample frames decoded at `sample_rate`;
and, optionally, `bits_per_sample`, an integer from 1 to 64. A method that
cannot count every frame MUST NOT propose a stream claim. A file with several
audio streams has one claim per stream. Claims of different runs may
disagree; all of them remain and none is current.

**Span suggestions.** A span suggestions Claim holds one run's suggested spans
for one source. It MUST pin exactly one `source` Artifact revision, exactly one
`stream` audio stream version 1 Claim revision whose `artifact` reference pins
that same Artifact object and revision, and exactly one `run` derivation run
version 1 Claim revision. Its payload MUST contain exactly `sample_rate`, equal
to the stream claim's `sample_rate`, and `spans`, 1 to 10,000 objects of
exactly integer `start_sample` and `end_sample` and a `class` of `music`,
`talk`, `quiet`, or `unknown`. Every span MUST satisfy
`0 <= start_sample < end_sample <= sample_count`, where `sample_count` is the
pinned stream claim's, and every span MUST start at or after the previous
span's end. A suggestion is never a selection: it accepts no audio and names
no excerpt. A suggestion is never current, and a later run's suggestions do
not supersede an earlier run's.

**Matches and alignments.** `archive.claim/work_match` version 2 is version 1
with two changes: its `run` reference MUST pin a derivation run version 1
Claim revision, and the compared audio MUST be exactly one reference, either
in role `occurrence`, pinning a song occurrence version 2 Claim revision, or
in role `selection`, pinning an audio selection version 2 Claim revision. Its
`corpus`, `work`, and `parameters` references, its payload, and the rule that
`occurrence` and `corpus` references name different objects are unchanged.
`archive.claim/capture_alignment` version 2 is version 1 whose optional `run`
reference MUST pin a derivation run version 1 Claim revision. Version 1 of
both remains installed with its exact bytes. A new match of an occurrence or
a selection MUST cite version 2 (D-16 adds version 3, which compares a window
of suggested spans): Door checks a version 1 match only structurally, as
before, and does not refuse one whose `run` pins any Claim. A match never establishes a Work.

**Specification upgrade.** An `archive.transition/specification_upgrade`
version 1 Claim moves an archive from its current specification to a named
later one. Its payload MUST contain exactly `from` and `to`, each
`archive/<version>`. It MUST pin exactly one `authorizer` person Entity
revision, which MUST be the current revision of the active operator's
appointed person. Door MAY commit an upgrade only when `from` is the archive's
specification and `to` names a later version whose catalog contains every
bundle of the current catalog with identical bytes, whose layout equals the
archive's layout, and whose core context defines every term the current one
defines, with the same mapping; any other upgrade MUST be refused as
unsupported. A committed upgrade MUST, in one commit under the archive lock,
verify each added bundle against the target catalog, install it under its
digest path, record the new version where the archive records its version,
and write the transition record citing the upgrade bundle. Expected heads,
author, and authorizer are checked as for every transition, a complete retry
changes nothing, and no downgrade exists. `door admit` MUST refuse the
contract as it refuses every transition contract, and a Door without an
upgrade handler MUST refuse every upgrade proposal as an unsupported
transition.

**Terms.** The payload field `software` and the reference roles `artifact` and
`source` are core terms; these contracts' context fragments do not redefine
them. Each fragment maps its own payload fields and roles; `run` maps to
`prov:wasGeneratedBy`, `used` to `prov:used`, and `started_at` and `ended_at`
to `prov:startedAtTime` and `prov:endedAtTime`.

**Profile rules.** A reference target MAY name `shapes`, a set of at least two
record shapes, in place of `shape`; a reference in that role MUST pin a
revision of one of them. `x-archive-ordered-instants` names two payload
fields, `start` and `end`, that MUST be RFC 3339 instants in UTC with a `Z`
suffix, the second not earlier than the first. `x-archive-sequential-spans`
names a nonempty payload array and its integer `start` and `end` fields; every
element MUST have `0 <= start < end` and MUST start at or after the previous
element's end. `x-archive-target-measure` names a `role` whose one reference
pins a Claim, a `rate` field whose value in this payload MUST equal its value
in the pinned Claim's payload, the pinned Claim's `count` field, and a
`payload` array whose `end` fields MUST NOT exceed that count.
`x-archive-same-subject` names a `role` whose one reference pins a Claim, a
`subject` role of that Claim, and an `equals` role of this record; the pinned
Claim's one `subject` reference and this record's one `equals` reference MUST
pin the same object and revision. The roles these two rules name MUST have a
reference target rule for exactly one Claim. The JSON Schema keyword `maximum`
is supported. These rules are closed and root-only like the earlier profile
rules: an unknown or malformed rule MUST be refused when the bundle loads, and
a generic schema success MUST NOT bypass them. This section defines no
`current` rule.

### D-16. Work matches over a window of suggested spans (ADR 0072)

Specification 0.0.8 installs version 3 of `archive.claim/work_match`. Every
0.0.7 contract bundle remains installed with its exact bytes, including
version 2 of `archive.claim/work_match`, and the layout, the entity kinds, and
the core context are those of 0.0.7. The release is additive, so a 0.0.7
archive adopts it by the specification upgrade of D-15.

**Window matches.** A work match version 3 compares a window of one span
suggestions Claim with the occurrences of one Work. It MUST pin exactly one
`suggestions` reference to a span suggestions version 1 Claim revision,
exactly one `work` reference to a `musical_work` Entity revision, one or more
`corpus` references to song occurrence version 2 Claim revisions that name
different objects, exactly one `run` reference to a derivation run version 1
Claim revision, and at most one `parameters` Artifact revision. Its payload
MUST contain exactly the version 2 fields `method`, `score`, `scale`, and
`direction`, with their version 2 rules, and `sample_rate`, a positive
integer equal to the pinned suggestions' `sample_rate`, and `window`, an
object of exactly integer `start_sample` and `end_sample`. `start_sample`
MUST equal the `start_sample` of one span of the pinned suggestions, and
`end_sample` MUST equal the `end_sample` of that span or of a later one, so a
window is a run of whole suggested spans within the stream they were measured
against. A window is a suggestion like the spans it is cut from: it accepts no
audio and names no excerpt, and a person accepts audio by proposing an audio
selection. Version 2 remains the form for a match of a whole occurrence or
selection; neither version supersedes the other. A match never establishes a
Work, and implementations MUST NOT read a match as identity or as current.

**Terms.** The fragment maps `suggestions` to `arch:compared_suggestions`,
`window` to `arch:window`, and `sample_rate`, `start_sample`, and
`end_sample` to the terms span suggestions version 1 uses; its other terms
are those of version 2.

**Profile rule.** `x-archive-span-window` names a `role` whose one reference
pins a Claim, a `rate` field whose value in this payload MUST equal its value
in the pinned Claim's payload, a `payload` object of this record with integer
fields `start` and `end`, and a `spans` array of the pinned Claim whose
elements have integer `start` and `end` fields. The `start` of this record's
object MUST equal the `start` of some element of that array, and its `end`
MUST equal the `end` of that element or of a later one. The role MUST have a
reference target rule for exactly one Claim, and `start` and `end` MUST name
different fields. The rule is closed and root-only like the earlier profile
rules: an unknown or malformed rule MUST be refused when the bundle loads,
and a generic schema success MUST NOT bypass it. The existing
`x-archive-ordered-distinct-refs` rule keeps the `corpus` references
distinct. This section defines no `current` rule.

### D-17. Chords, meter, contributions, and band membership (ADRs 0077 and 0078)

Specification 0.0.9 installs version 1 of `archive.claim/chord_stacks`,
`archive.claim/chord_sequence`, `archive.claim/meter_layers`,
`archive.claim/contribution`, and `archive.claim/band_membership`. Every 0.0.8 contract bundle remains
installed with its exact bytes, and the layout is `archive/2`. The core
context is that of 0.0.8 with the Entity kinds `release` and `recording`
added (D-8); it drops and changes no term, so the release is additive and a
0.0.8 archive adopts it by the specification upgrade of D-15. None of these
contracts defines a `current` rule, and none is current by kind or asserter.

**Asserted from the audio and from documents.** A chord stacks or meter
layers Claim is one asserter's reading of one audio stream. It MUST pin
exactly one `source` Artifact revision, the audio whose samples it counts;
exactly one `stream`, an audio stream version 1 Claim revision whose
`artifact` reference pins that same Artifact object and revision; at most one
`run`, a derivation run version 1 Claim revision, which a machine's claim
pins; at most one `document`, the Artifact revision of an outside source's
bytes; and zero to eight `analysed` Artifact revisions, audio the asserter
analysed in place of the source, such as a separated stem, whose samples
share the source's timeline. A chord stacks Claim MAY also pin at most one
`sequence`, a chord sequence version 1 Claim revision an alignment placed on
this recording, and zero to eight `corrects`, chord stacks version 1 Claim
revisions it corrects. Its payload's `sample_rate` MUST equal the stream's,
and no span, bar, or beat ends after the stream's `sample_count`. A claim
that pins a `document` or a `sequence` MUST state `alignment`, an object of
`method` (1 to 200 characters; `manual` for a person's placement) and
optional integer `max_error_samples` of at least 0, and any other MUST NOT. A
claim that pins a `document` MAY give `document_url`, an `http` or
`https` URL of at most 2,000 characters drawn from the characters RFC 3986
allows (letters, digits, and `-._~:/?#[]@!$&'()*+,;=%`); and its spans or
bars MAY carry a `locator`, the document's own numbering. A claim that pins
no document MUST NOT have either. The URL says where the document came
from; the pinned Artifact is the evidence. Pitch classes are integers 0 to
11, C = 0, in twelve-tone equal temperament, each the nearest step of the
grid in force at its samples (D-18's tuning).

**Chord stacks.** The payload MUST contain `sample_rate` and `spans`, and MAY
contain `part` (1 to 100 characters naming the part described),
`weight_scale` (1 to 200 characters naming what weights mean), `hold_rule`,
`alignment`, and `document_url`. `spans` holds 1 to 10,000 objects of
integer `start_sample` and `end_sample`, `readings`, and optional `end`,
`entry`, and `locator` (integer `bar` and optional `beat`, each at least 1);
every span MUST satisfy `0 <= start_sample < end_sample` and start at or
after the previous span's end. A span's `end` is `asserted` (also when
absent) or `held`: the source gave only a start and the asserter held the
chord by its own rule, which the payload states as `hold_rule` (1 to 500
characters) exactly when some span is held. A held end is weaker evidence of
where the chord stops than an asserted one. A span's `entry`, allowed only
when the claim pins a `sequence`, is the 0-based position of the sequence
entry it was placed from and MUST be a position in that sequence's
`entries`. `readings` holds 1 to 16 distinct readings in the asserter's order
of preference, each exactly one of:

- a stack: `bass`, a pitch class; `tones`, 0 to 12 pitch classes in strictly
  ascending order, the classes sounding above the bass; optional `root`, a
  pitch class that need not sound; optional `uncertain`, 1 to 4 groups of
  `pitch_classes` (strictly ascending) and `present`, where `exactly_one`
  needs 2 to 12 classes and `maybe` 1 to 12; and optional `voicing`, 1 to 32
  strictly ascending MIDI note numbers 0 to 127. No uncertain class is the
  bass, a tone, or in another group, and the groups are in ascending order of
  their `pitch_classes` compared item by item. The voicing's lowest note's
  class is the bass, and the classes of its other notes include every tone
  and nothing outside the tones and uncertain classes;
- no chord: `no_chord`, which MUST be `true`;
- text only: the source's `text`, uninterpreted.

Every form MAY carry `text` (1 to 200 characters, the source's symbol
verbatim), and carries `weight`, exact decimal text, exactly when the payload
states `weight_scale`. A weight compares readings within its own claim only.
A chord stacks claim MAY also pin at most one `contribution`, a contribution
version 1 Claim revision saying whose part the stacks describe (ADR 0078).
That contribution is meant to be about the stack's recording, and an
`analysed` Artifact to share the source's timeline; this release checks
neither, so both rest on the asserter.
A correction is its own claim citing in `corrects` what it corrects; it
replaces nothing. Names (Harte strings, lead-sheet symbols) are computed
views and never stored.

**Chord sequences.** A chord sequence Claim holds chords in a source's
written order with no times. It MUST pin at least one of a `work`, a
`musical_work` Entity revision, and a `document` Artifact revision, and MAY
pin at most one `run` and zero to eight `corrects`, chord sequence version 1
Claim revisions it corrects. It pins no audio and its payload has no
`sample_rate` and no `alignment`. Its payload MUST contain `entries` and MAY
contain `part`, `weight_scale`, `document_url` (with a document only), and
`repeats`. `entries` holds 1 to 10,000 objects of `readings` (as a chord
span's, with the same weight rule), optional `section` (1 to 100 characters,
the source's section label), and optional `locator` (with a document only):
an integer `line` and at most one of integer `word`, `syllable`, or `column`,
each at least 1. Located entries MUST NOT go backwards in the document,
compared by line and then by the word, syllable, or column given (0 when
none). `repeats` holds 0 to 1,000 objects of integer `first` and `last`,
positions in `entries` with `first <= last`, and `times` (2 to 999); they
MUST be distinct and in order of `first`, the longer of two with the same
`first` first, and two ranges MUST nest or be apart, never cross. A sequence
holds no lyric text: a locator points into the document.

**Meter layers.** The payload MUST contain `sample_rate` and `layers`, and MAY
contain `modulations`, `alignment`, and `document_url`. `layers` holds 1 to 8
simultaneous layers, none primary, each of optional `label` (1 to 100
characters), optional `role` (`written` or `felt`; a claim with a `felt`
layer MUST have a `written` one), and exactly one of `bars` and `beats`. A
bars layer holds 1 to 10,000 bars, each starting at or after the end of the
previous bar of its layer. A bar has integer `start_sample` and `end_sample`
with `0 <= start_sample < end_sample`, `numerator` (1 to 64), `denominator`
(1, 2, 4, 8, 16, 32, or 64), optional `grouping` (1 to 64 positive integers
whose sum MUST equal the numerator), optional `beats`, and optional `locator`
(integer `bar` of at least 1). A bar's `beats` holds the sample of each
counted beat, one per group, or one per numerator unit without a grouping;
the first MUST equal the bar's `start_sample`, each MUST be later than the
one before, and every one MUST be before `end_sample`. A beat-only layer
asserts no meter: `beats` holds 1 to 100,000 strictly ascending samples, each
before the stream's `sample_count`; optional `downbeats` holds strictly
ascending samples, each one of its `beats`; and optional `beat_unit` is a
note value. `modulations` holds 0 to 10,000 links, distinct and in ascending
order of `layer` and then `bar`, each of `layer` (the 0-based position of a
bars layer), `bar` (the 0-based position of a bar in it, at least 1), and
`kind`: an `equation` has `before` and `after` note values (`denominator`
from the bar denominators and optional `dots`, 0 to 2), saying the note value
before under the old tempo lasts as long as the one after under the new; a
`regrouping` has `pulse_before` and `pulse_after`, note values of different
lengths, saying every note value keeps its length and the felt pulse moves.
The bar before `bar` MUST end where `bar` starts. The tempo ratio and tempo
are derived, never stored.

**Contributions.** A contribution Claim says that one contributor took part in
one role in one scope. Its payload MUST contain `scope`, one of `work`,
`release`, `recording`, `span`, or `channel`, and MAY contain `role`, `text`
(1 to 1,000 characters, the credit exactly as the source printed it),
`credited_as` (1 to 200 characters, the contributor's name as printed), the
scope's fields below, and the asserter fields. It MAY pin at most one
`contributor`, a `person` or `organisation` Entity revision, at most one
`run`, a derivation run version 1 Claim revision, and at most one `document`
Artifact revision. A `recording` reference MAY pin a `recording` Entity, an
audio selection version 2 or song occurrence version 2 Claim, or an Artifact.
Each scope MUST have exactly these other references and fields:

| Scope | References | Payload |
|---|---|---|
| `work` | one `work`, a `musical_work` Entity | nothing more |
| `release` | one `release`, a `release` Entity | nothing more |
| `recording` | one `recording` | nothing more |
| `span` | one `source` Artifact, one `stream`, at most one `recording` | `sample_rate` and `spans` |
| `channel` | one `source` Artifact, at most one `stream`, at most one `recording` | optional `channel` |

A `stream` MUST be an audio stream version 1 Claim revision whose `artifact`
reference pins the `source` Artifact object and revision. A span claim's
`sample_rate` MUST equal the stream's, and its `spans` hold 1 to 1,000 objects
of exactly integer `start_sample` and `end_sample`, ordered and not
overlapping as chord spans are, none ending after the stream's
`sample_count`. A span claim that pins a document MUST state `alignment` as a
chord stacks claim does, and one that pins none MUST NOT. A channel claim's
`channel` is the 1-based index of one channel of a multichannel source; it
needs a `stream`, and it MUST NOT exceed the stream's `channel_count`.

`role` is a closed object of `type`, the name of a MusicBrainz relationship
type for the entity pair the scope implies (artist-work for `work`;
artist-release and place-release for `release`; artist-recording and
place-recording for `recording` and `span`; artist-recording for `channel`),
or the local type `label` for a release; and, as the type takes them,
`attributes` (its MusicBrainz attributes, distinct and in ascending code
point order), `instruments` (1 to 16 distinct
`https://musicbrainz.org/instrument/<MBID>` URIs, required by `instrument`),
and `vocals` (distinct MusicBrainz vocal attributes). The types, their
attributes, and which take instruments or vocals are the scheme at the end
of this section; the contract bundle encodes the same table. A claim without
`role` MUST keep `text`. A claim without a contributor MUST have a `role`. A
place type (`recorded at`, `mastered at`, and the other `... at` types) MUST
NOT pin a contributor and MUST keep `text`, because no Entity kind holds a
place yet. `credited_as` needs a contributor or a place type.

The asserter fields are `confidence` (exactly `scale`, 1 to 200 characters,
and `value`, exact decimal text, compared only within its scale),
`document_url` (as above), `retrieved_at` (an RFC 3339 full date or a UTC
instant with a `Z` suffix whose date is a calendar date), and `locator` (1 to
200 characters naming where in the document). A claim that pins a document
MUST keep `text`; `document_url`, `retrieved_at`, and `locator` MUST NOT
appear without a document.

**Band membership.** A band membership Claim states MusicBrainz's `member of
band`. It MUST pin exactly one `band`, an `organisation` Entity revision, and
exactly one `member`, a `person` Entity revision of a different object, and
at most one `run` and one `document` as a contribution does. Its payload is a
closed object that MAY contain `attributes` (distinct, ascending, from
`additional`, `eponymous`, and `original`), `instruments` and `vocals` as a
role's, `from` and `to` (partial dates `YYYY`, `YYYY-MM`, or `YYYY-MM-DD`
naming a calendar year, month, or day from year 1), `ended` (`true`, only
when `to` is absent), `text`, `credited_as`, and the asserter fields with the
same document rules. The last day `to` can mean MUST NOT be before the first
day `from` can mean.

**Terms.** Each fragment maps its payload fields and roles; `run` maps to
`prov:wasGeneratedBy`, `document` to `dcterms:source`, and `stream` to
`arch:measured_stream` as in D-15. The roles `source`, `release`, and
`recording` are core terms (`release` and `recording` also name the new
kinds); no fragment redefines a core term. A meter layer's `role` and a
modulation's `kind` are payload fields that share a core term's name; no
fragment maps them.

**JSON Schema keywords.** The evaluator also supports `anyOf` (a nonempty
array of schemas, at least one of which the instance satisfies), `not`,
`if` with optional `then` and `else` (a `then` or `else` without `if` is
refused when the bundle loads), and `uniqueItems` (JSON equality). Every
branch is evaluated, so a malformed branch is refused even when it does not
decide the result.

**Profile rules.** A path is a nonempty array of payload field names; every
array met on the way is entered element by element, and a missing field
reaches nothing.

- A reference target rule MAY name `any`, at least two distinct ordinary
  target rules, none naming `any` or `many`; a reference in the role MUST
  satisfy one of them. `many` then belongs to the role.
- `x-archive-sequential-spans` and `x-archive-target-measure` MAY name
  `within`, a payload array whose elements each hold the rule's `payload`
  array; order is then checked within each element, never across them,
  every element of every such array is measured, and an element without the
  array has nothing to check. They and
  `x-archive-same-subject` MAY say `optional: true` (no other value): the
  span rules then apply only when the payload has their array (or `within`),
  and the same-subject rule only when the record has a reference in `role`.
- `x-archive-ascending-items` names `paths` to arrays whose items MUST be all
  integers in strictly ascending numeric order, or all strings in strictly
  ascending code point order.
- `x-archive-pitch-stacks` names a `payload` path to readings and the fields
  `bass`, `tones`, `uncertain`, `classes`, and `voicing`; every reading with
  a `bass` MUST satisfy the uncertain-tone and voicing rules stated for chord
  stacks above.
- `x-archive-bar-beats` names a `payload` path to bars and the fields
  `start`, `end`, `numerator`, `grouping`, and `beats`, with the grouping and
  beat rules stated for meter layers above.
- `x-archive-metric-modulations` names the `payload` array of links, the
  `layers` and `bars` arrays they address, the bar's `start` and `end`, the
  link's `layer`, `bar`, `pulse_before`, and `pulse_after`, and the note
  value's `denominator` and `dots`: each link addresses a bar after the first
  of a layer that holds `bars`, that bar's predecessor ends where it starts,
  links are distinct and in order, and a link with both pulses names note
  values of different lengths.
- `x-archive-subset` names a `payload` path and two arrays, `items` and
  `of`: on every object reached, each value of `items` MUST be a value of
  `of`.
- `x-archive-target-samples` names a `role` whose one reference pins a Claim,
  that Claim's integer `count`, and `paths`: every integer reached MUST be
  less than `count`.
- `x-archive-target-index` names a `role` whose one reference pins a Claim, a
  `path`, and that Claim's payload array `array`: when the path reaches an
  integer, the record MUST pin exactly one reference in `role`, and every
  integer reached MUST be a position in `array`.
- `x-archive-document-order` names a payload array `payload`, its elements'
  `locator`, the locator's integer `line`, and `within`, the fields compared
  after the line: elements with a locator MUST NOT go backwards.
- `x-archive-nested-ranges` names a payload array `payload` of ranges with
  integer `first` and `last` over the positions of the payload array `of`,
  with the order and nesting rules stated for repeats above.
- `x-archive-target-bound` names a `role` whose one reference pins a Claim, a
  payload `field`, and that Claim's payload field `bound`: when the payload
  has `field`, the record MUST pin exactly one reference in `role`, and
  `field` MUST NOT exceed `bound`.
- `x-archive-partial-dates` names payload `fields` whose values, when present,
  begin with a calendar date written `YYYY`, `YYYY-MM`, or `YYYY-MM-DD` with a
  year of at least 1 (what follows a `T` is the schema's to check), and
  optionally `start` and `end`, two of those fields: the last day `end` can
  mean MUST NOT be before the first day `start` can mean.

Each rule names distinct fields, is closed and root-only like the earlier
profile rules, and an unknown or malformed rule MUST be refused when the
bundle loads; a generic schema success MUST NOT bypass it. No earlier
bundle's rules change meaning: `within`, `optional`, and `any` are new keys.

**The role scheme.** This is the vocabulary a contribution's `role` and a band
membership's attributes and vocals draw from, as SKOS. Each role type is a
concept whose notation is its name, in the scheme of its entity pair, and
`skos:exactMatch` the MusicBrainz relationship type with that identifier, so
a renamed MusicBrainz type keeps its meaning. `arch:takesAttribute`,
`arch:takesInstruments`, and `arch:takesVocals` say what each type accepts.
Each attribute a type takes, and each vocal attribute, is a concept whose
notation is its name, in the scheme `attributes` or `vocals`, and
`skos:exactMatch` the MusicBrainz relationship attribute with that
identifier; `arch:takesAttribute` names an attribute concept by its
notation. The local type `label` has no MusicBrainz match.
MusicBrainz's relationship types, attributes, and instruments are CC0 data,
read from musicbrainz.org on 2026-10-18.

```turtle
@prefix arch: <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix mbrel: <https://musicbrainz.org/relationship/> .
@prefix mbattr: <https://musicbrainz.org/relationship-attribute/> .

arch:roles-artist-artist a skos:ConceptScheme ; skos:notation "artist-artist" .
arch:role-artist-artist-member_of_band a skos:Concept ;
  skos:inScheme arch:roles-artist-artist ;
  skos:notation "member of band" ;
  skos:exactMatch mbrel:5be4c609-9afa-4ea0-910b-12ffb71e3821 ;
  arch:takesAttribute "additional", "eponymous", "original" ;
  arch:takesInstruments true ;
  arch:takesVocals true .

arch:roles-artist-recording a skos:ConceptScheme ; skos:notation "artist-recording" .
arch:role-artist-recording-arranger a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "arranger" ;
  skos:exactMatch mbrel:22661fb8-cdb7-4f67-8385-b2a8be6c9f0d ;
  arch:takesAttribute "additional", "associate", "co" .
arch:role-artist-recording-audio a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "audio" ;
  skos:exactMatch mbrel:ca8d6d99-b847-439c-b0ec-33d8a1b942bc ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-recording-balance a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "balance" ;
  skos:exactMatch mbrel:0748fa55-56b5-4ad5-8ce8-15b97f82a0c2 ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-recording-chorus_master a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "chorus master" ;
  skos:exactMatch mbrel:45115945-597e-4cb9-852f-4e6ba583fcc8 ;
  arch:takesAttribute "additional", "assistant" .
arch:role-artist-recording-concertmaster a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "concertmaster" ;
  skos:exactMatch mbrel:ffeaa74f-8295-45ee-a2f2-7c0cc1f73b1e ;
  arch:takesAttribute "guest" .
arch:role-artist-recording-conductor a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "conductor" ;
  skos:exactMatch mbrel:234670ce-5f22-4fd0-921b-ef1662695c5d ;
  arch:takesAttribute "additional", "assistant" .
arch:role-artist-recording-editor a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "editor" ;
  skos:exactMatch mbrel:40dff87a-e475-4aa6-b615-9935b564d756 ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-recording-engineer a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "engineer" ;
  skos:exactMatch mbrel:5dcc52af-7064-4051-8d62-7d80f4c3c907 ;
  arch:takesAttribute "additional", "assistant", "associate", "co", "executive" .
arch:role-artist-recording-field_recordist a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "field recordist" ;
  skos:exactMatch mbrel:fc9b963a-29fa-4949-b22a-3bffd2440024 .
arch:role-artist-recording-instrument a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "instrument" ;
  skos:exactMatch mbrel:59054b12-01ac-43ee-a618-285fd397e461 ;
  arch:takesAttribute "additional", "guest", "solo" ;
  arch:takesInstruments true .
arch:role-artist-recording-instrument_arranger a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "instrument arranger" ;
  skos:exactMatch mbrel:4820daa1-98d6-4f8b-aa4b-6895c5b79b27 ;
  arch:takesAttribute "additional", "associate", "co" ;
  arch:takesInstruments true .
arch:role-artist-recording-instrument_technician a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "instrument technician" ;
  skos:exactMatch mbrel:e88fefc3-042b-4f28-83af-6a79793b630b ;
  arch:takesInstruments true .
arch:role-artist-recording-mix a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "mix" ;
  skos:exactMatch mbrel:3e3102e1-1896-4f50-b5b2-dd9824e46efe ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-recording-orchestrator a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "orchestrator" ;
  skos:exactMatch mbrel:38fa7405-f9a5-48cb-827a-8ac601933ba0 ;
  arch:takesAttribute "additional" .
arch:role-artist-recording-performer a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "performer" ;
  skos:exactMatch mbrel:628a9658-f54c-4142-b0c0-95f031b544da ;
  arch:takesAttribute "additional", "guest", "solo" .
arch:role-artist-recording-performing_orchestra a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "performing orchestra" ;
  skos:exactMatch mbrel:3b6616c5-88ba-4341-b4ee-81ce1e6d7ebb ;
  arch:takesAttribute "additional" .
arch:role-artist-recording-producer a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "producer" ;
  skos:exactMatch mbrel:5c0ceac3-feb4-41f0-868d-dc06f6e27fc0 ;
  arch:takesAttribute "additional", "assistant", "associate", "co", "executive" .
arch:role-artist-recording-programming a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "programming" ;
  skos:exactMatch mbrel:36c50022-44e0-488d-994b-33f11d20301e ;
  arch:takesAttribute "additional", "assistant", "associate" ;
  arch:takesInstruments true .
arch:role-artist-recording-recording a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "recording" ;
  skos:exactMatch mbrel:a01ee869-80a8-45ef-9447-c59e91aa7926 ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-recording-remixer a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "remixer" ;
  skos:exactMatch mbrel:7950be4d-13a3-48e7-906b-5af562e39544 ;
  arch:takesAttribute "additional", "assistant" .
arch:role-artist-recording-sound a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "sound" ;
  skos:exactMatch mbrel:0cd6aa63-c297-42ed-8725-c16d31913a98 ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-recording-vocal a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "vocal" ;
  skos:exactMatch mbrel:0fdbe3c6-7700-4a31-ae54-b53f06ae1cfa ;
  arch:takesAttribute "additional", "guest", "solo" ;
  arch:takesVocals true .
arch:role-artist-recording-vocal_arranger a skos:Concept ;
  skos:inScheme arch:roles-artist-recording ;
  skos:notation "vocal arranger" ;
  skos:exactMatch mbrel:8a2799e8-a7e2-41ce-a7da-b5f520687216 ;
  arch:takesAttribute "additional", "associate", "co" ;
  arch:takesVocals true .

arch:roles-artist-release a skos:ConceptScheme ; skos:notation "artist-release" .
arch:role-artist-release-arranger a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "arranger" ;
  skos:exactMatch mbrel:34d5334e-a4c8-4b65-a5f8-bbcc9c81d13d ;
  arch:takesAttribute "additional", "associate", "co" .
arch:role-artist-release-art_direction a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "art direction" ;
  skos:exactMatch mbrel:f3b80a09-5ebf-4ad2-9c46-3e6bce971d1b ;
  arch:takesAttribute "additional", "assistant" .
arch:role-artist-release-audio a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "audio" ;
  skos:exactMatch mbrel:b04848d7-dbd9-4be0-9d8c-13df6d6e40db ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-release-balance a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "balance" ;
  skos:exactMatch mbrel:97169e5e-c978-486e-a5ea-da353ca9ea42 ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-release-booklet_editor a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "booklet editor" ;
  skos:exactMatch mbrel:74518a6b-589d-460e-8dd7-a8383851040a .
arch:role-artist-release-chorus_master a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "chorus master" ;
  skos:exactMatch mbrel:b9129850-73ec-4af5-803c-1c12b97e25d2 ;
  arch:takesAttribute "additional", "assistant" .
arch:role-artist-release-compiler a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "compiler" ;
  skos:exactMatch mbrel:2f81887a-8674-4d8b-bd48-8bfd4c6fa332 .
arch:role-artist-release-conductor a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "conductor" ;
  skos:exactMatch mbrel:9ae9e4d0-f26b-42fb-ab5c-1149a47cf83b ;
  arch:takesAttribute "additional", "assistant" .
arch:role-artist-release-copyright a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "copyright" ;
  skos:exactMatch mbrel:730b5251-7432-4896-8fc6-e1cba943bfe1 .
arch:role-artist-release-design a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "design" ;
  skos:exactMatch mbrel:9c02ea37-7680-4fb5-8555-e330c7aa885b ;
  arch:takesAttribute "additional", "assistant" .
arch:role-artist-release-editor a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "editor" ;
  skos:exactMatch mbrel:f30c29d3-a3f1-420d-9b6c-a750fd6bc2aa ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-release-engineer a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "engineer" ;
  skos:exactMatch mbrel:87e922ba-872e-418a-9f41-0a63aa3c30cc ;
  arch:takesAttribute "additional", "assistant", "associate", "co", "executive" .
arch:role-artist-release-graphic_design a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "graphic design" ;
  skos:exactMatch mbrel:cf43b79e-3299-4b0c-9244-59ea06337107 ;
  arch:takesAttribute "additional" .
arch:role-artist-release-illustration a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "illustration" ;
  skos:exactMatch mbrel:a6029157-d96b-4dc3-9f73-f99f76423d11 ;
  arch:takesAttribute "additional" .
arch:role-artist-release-instrument a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "instrument" ;
  skos:exactMatch mbrel:67555849-61e5-455b-96e3-29733f0115f5 ;
  arch:takesAttribute "additional", "guest", "solo" ;
  arch:takesInstruments true .
arch:role-artist-release-instrument_arranger a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "instrument arranger" ;
  skos:exactMatch mbrel:18f159bb-44f0-4aef-b198-a4736ad9b659 ;
  arch:takesAttribute "additional", "associate", "co" ;
  arch:takesInstruments true .
arch:role-artist-release-instrument_technician a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "instrument technician" ;
  skos:exactMatch mbrel:bd780597-0b67-4b97-a4a0-671a70e1182d ;
  arch:takesInstruments true .
arch:role-artist-release-lacquer_cut a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "lacquer cut" ;
  skos:exactMatch mbrel:904e57f3-cbbc-43ab-8798-13e710e400d3 .
arch:role-artist-release-licensor a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "licensor" ;
  skos:exactMatch mbrel:eaaf08cf-a698-4e3b-a871-f9570f8fdab1 .
arch:role-artist-release-liner_notes a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "liner notes" ;
  skos:exactMatch mbrel:01323b4f-7aba-410c-8c91-cb224b963a40 ;
  arch:takesAttribute "additional", "translator" .
arch:role-artist-release-mastering a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "mastering" ;
  skos:exactMatch mbrel:84453d28-c3e8-4864-9aae-25aa968bcf9e ;
  arch:takesAttribute "additional", "assistant", "associate", "co", "pre", "re" .
arch:role-artist-release-mix a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "mix" ;
  skos:exactMatch mbrel:6cc958c0-533b-4540-a281-058fbb941890 ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-release-orchestrator a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "orchestrator" ;
  skos:exactMatch mbrel:04e1f0b6-ef40-4168-ba25-42a87729fe09 ;
  arch:takesAttribute "additional" .
arch:role-artist-release-performer a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "performer" ;
  skos:exactMatch mbrel:888a2320-52e4-4fe8-a8a0-7a4c8dfde167 ;
  arch:takesAttribute "additional", "guest", "solo" .
arch:role-artist-release-performing_orchestra a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "performing orchestra" ;
  skos:exactMatch mbrel:23a2e2e7-81ca-4865-8d05-2243848a77bf ;
  arch:takesAttribute "additional" .
arch:role-artist-release-phonographic_copyright a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "phonographic copyright" ;
  skos:exactMatch mbrel:01d3488d-8d2a-4cff-9226-5250404db4dc .
arch:role-artist-release-photography a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "photography" ;
  skos:exactMatch mbrel:0b58dc9b-9c49-4b19-bb58-9c06d41c8fbf ;
  arch:takesAttribute "additional" .
arch:role-artist-release-producer a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "producer" ;
  skos:exactMatch mbrel:8bf377ba-8d71-4ecc-97f2-7bb2d8a2a75f ;
  arch:takesAttribute "additional", "assistant", "associate", "co", "executive" .
arch:role-artist-release-programming a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "programming" ;
  skos:exactMatch mbrel:617063ad-dbb5-4877-9ba0-ba2b9198d5a7 ;
  arch:takesAttribute "additional", "assistant", "associate" ;
  arch:takesInstruments true .
arch:role-artist-release-recording a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "recording" ;
  skos:exactMatch mbrel:023a6c6d-80af-4f88-ae69-f5f6213f9bf4 ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-release-remixer a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "remixer" ;
  skos:exactMatch mbrel:ac6a86db-f757-4815-a07e-744428d2382b ;
  arch:takesAttribute "additional", "assistant" .
arch:role-artist-release-sound a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "sound" ;
  skos:exactMatch mbrel:271306ca-c77f-4fe0-94bc-dd4b87ae0205 ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-release-transfer a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "transfer" ;
  skos:exactMatch mbrel:9fe3ca27-f9df-4e06-ab2d-84947f3f897e ;
  arch:takesAttribute "additional", "assistant", "associate", "co" .
arch:role-artist-release-vocal a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "vocal" ;
  skos:exactMatch mbrel:eb10f8a0-0f4c-4dce-aa47-87bcb2bc42f3 ;
  arch:takesAttribute "additional", "guest", "solo" ;
  arch:takesVocals true .
arch:role-artist-release-vocal_arranger a skos:Concept ;
  skos:inScheme arch:roles-artist-release ;
  skos:notation "vocal arranger" ;
  skos:exactMatch mbrel:d7d9128d-e676-4d8f-a353-f48a55a98501 ;
  arch:takesAttribute "additional", "associate", "co" ;
  arch:takesVocals true .

arch:roles-artist-work a skos:ConceptScheme ; skos:notation "artist-work" .
arch:role-artist-work-adapter a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "adapter" ;
  skos:exactMatch mbrel:89fff34a-2c51-4068-9709-50d6c6dd3d01 ;
  arch:takesAttribute "additional" .
arch:role-artist-work-arranger a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "arranger" ;
  skos:exactMatch mbrel:d3fd781c-5894-47e2-8c12-86cc0e2c8d08 ;
  arch:takesAttribute "additional", "associate", "co" .
arch:role-artist-work-composer a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "composer" ;
  skos:exactMatch mbrel:d59d99ea-23d4-4a80-b066-edca32ee158f ;
  arch:takesAttribute "additional" .
arch:role-artist-work-instrument_arranger a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "instrument arranger" ;
  skos:exactMatch mbrel:0084e70a-873e-4f7f-b3ff-635b9e863dae ;
  arch:takesAttribute "additional", "associate", "co" ;
  arch:takesInstruments true .
arch:role-artist-work-librettist a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "librettist" ;
  skos:exactMatch mbrel:7474ab81-486f-40b5-8685-3a4f8ea624cb ;
  arch:takesAttribute "additional" .
arch:role-artist-work-lyricist a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "lyricist" ;
  skos:exactMatch mbrel:3e48faba-ec01-47fd-8e89-30e81161661c ;
  arch:takesAttribute "additional" .
arch:role-artist-work-orchestrator a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "orchestrator" ;
  skos:exactMatch mbrel:0a1771e1-8639-4740-8a43-bdafc050c3da ;
  arch:takesAttribute "additional" .
arch:role-artist-work-revised_by a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "revised by" ;
  skos:exactMatch mbrel:eeb9c319-9fde-4313-b76d-29db1576aad8 .
arch:role-artist-work-translator a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "translator" ;
  skos:exactMatch mbrel:da6c5d8a-ce13-474d-9375-61feb29039a5 ;
  arch:takesAttribute "additional" .
arch:role-artist-work-vocal_arranger a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "vocal arranger" ;
  skos:exactMatch mbrel:6a88b92b-8fb5-41b3-aa1f-169ee7e05ed6 ;
  arch:takesAttribute "additional", "associate", "co" ;
  arch:takesVocals true .
arch:role-artist-work-writer a skos:Concept ;
  skos:inScheme arch:roles-artist-work ;
  skos:notation "writer" ;
  skos:exactMatch mbrel:a255bca1-b157-4518-9108-7b147dc3fc68 ;
  arch:takesAttribute "additional" .

arch:roles-label-release a skos:ConceptScheme ; skos:notation "label-release" .
arch:role-label-release-label a skos:Concept ;
  skos:inScheme arch:roles-label-release ;
  skos:notation "label" .

arch:roles-place-recording a skos:ConceptScheme ; skos:notation "place-recording" .
arch:role-place-recording-arranged_at a skos:Concept ;
  skos:inScheme arch:roles-place-recording ;
  skos:notation "arranged at" ;
  skos:exactMatch mbrel:ce8dbff2-0f07-45f3-8b7c-2ea40d82cbfe .
arch:role-place-recording-edited_at a skos:Concept ;
  skos:inScheme arch:roles-place-recording ;
  skos:notation "edited at" ;
  skos:exactMatch mbrel:069c1cf0-a9b2-448e-8486-1eced48b48f9 .
arch:role-place-recording-engineered_at a skos:Concept ;
  skos:inScheme arch:roles-place-recording ;
  skos:notation "engineered at" ;
  skos:exactMatch mbrel:f845a95e-b2b5-4a94-9645-fc8b031ab0bd .
arch:role-place-recording-mixed_at a skos:Concept ;
  skos:inScheme arch:roles-place-recording ;
  skos:notation "mixed at" ;
  skos:exactMatch mbrel:11d74801-1493-4a5d-bc0f-4ddc537acddb .
arch:role-place-recording-produced_at a skos:Concept ;
  skos:inScheme arch:roles-place-recording ;
  skos:notation "produced at" ;
  skos:exactMatch mbrel:6bc3827d-bc20-4621-ae14-9c3707ad140a .
arch:role-place-recording-programmed_at a skos:Concept ;
  skos:inScheme arch:roles-place-recording ;
  skos:notation "programmed at" ;
  skos:exactMatch mbrel:e9771fa3-57ce-41cd-895f-52e70ced99cd .
arch:role-place-recording-recorded_at a skos:Concept ;
  skos:inScheme arch:roles-place-recording ;
  skos:notation "recorded at" ;
  skos:exactMatch mbrel:ad462279-14b0-4180-9b58-571d0eef7c51 .
arch:role-place-recording-remixed_at a skos:Concept ;
  skos:inScheme arch:roles-place-recording ;
  skos:notation "remixed at" ;
  skos:exactMatch mbrel:88183045-b769-48ba-a55c-c58450903241 .

arch:roles-place-release a skos:ConceptScheme ; skos:notation "place-release" .
arch:role-place-release-edited_at a skos:Concept ;
  skos:inScheme arch:roles-place-release ;
  skos:notation "edited at" ;
  skos:exactMatch mbrel:9b678c30-453a-47c4-8f69-9ba96b2c5e9c .
arch:role-place-release-engineered_at a skos:Concept ;
  skos:inScheme arch:roles-place-release ;
  skos:notation "engineered at" ;
  skos:exactMatch mbrel:b35aae66-5578-41d1-b34b-1c9b1897ad49 .
arch:role-place-release-lacquer_cut_at a skos:Concept ;
  skos:inScheme arch:roles-place-release ;
  skos:notation "lacquer cut at" ;
  skos:exactMatch mbrel:e87ae6d1-4453-4e73-aa29-86a6badc1f1d .
arch:role-place-release-manufactured_at a skos:Concept ;
  skos:inScheme arch:roles-place-release ;
  skos:notation "manufactured at" ;
  skos:exactMatch mbrel:3de9787f-c148-4b4f-b4bf-cb9a2616a092 .
arch:role-place-release-mastered_at a skos:Concept ;
  skos:inScheme arch:roles-place-release ;
  skos:notation "mastered at" ;
  skos:exactMatch mbrel:5d075afa-6bb8-4327-9528-e3e4d3d68f49 .
arch:role-place-release-mixed_at a skos:Concept ;
  skos:inScheme arch:roles-place-release ;
  skos:notation "mixed at" ;
  skos:exactMatch mbrel:8ebfc2f6-0ac7-40f6-b03e-67fe3428f5d4 .
arch:role-place-release-pressed_at a skos:Concept ;
  skos:inScheme arch:roles-place-release ;
  skos:notation "pressed at" ;
  skos:exactMatch mbrel:eabf5903-8467-4329-b905-7504336e8241 .
arch:role-place-release-produced_at a skos:Concept ;
  skos:inScheme arch:roles-place-release ;
  skos:notation "produced at" ;
  skos:exactMatch mbrel:1c73465d-9bf7-41f4-bcb6-fb1852ffa64c .
arch:role-place-release-recorded_at a skos:Concept ;
  skos:inScheme arch:roles-place-release ;
  skos:notation "recorded at" ;
  skos:exactMatch mbrel:3b1fae9f-5b22-42c5-a40c-d1e5c9b90251 .
arch:role-place-release-remixed_at a skos:Concept ;
  skos:inScheme arch:roles-place-release ;
  skos:notation "remixed at" ;
  skos:exactMatch mbrel:32af03cf-8879-4d07-b7c2-68c6cbf060bd .

arch:attributes a skos:ConceptScheme ; skos:notation "attributes" .
arch:attribute-additional a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "additional" ;
  skos:exactMatch mbattr:0a5341f8-3b1d-4f99-a0c6-26b7f4e42c7f .
arch:attribute-assistant a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "assistant" ;
  skos:exactMatch mbattr:8c4196b1-7053-4b16-921a-f22b2898ed44 .
arch:attribute-associate a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "associate" ;
  skos:exactMatch mbattr:8d23d2dd-13df-43ea-85a0-d7eb38dc32ec .
arch:attribute-co a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "co" ;
  skos:exactMatch mbattr:ac6f6b4c-a4ec-4483-a04e-9f425a914573 .
arch:attribute-eponymous a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "eponymous" ;
  skos:exactMatch mbattr:15eea3b8-5655-43c8-b2c2-dd03c901a79b .
arch:attribute-executive a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "executive" ;
  skos:exactMatch mbattr:e0039285-6667-4f94-80d6-aa6520c6d359 .
arch:attribute-guest a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "guest" ;
  skos:exactMatch mbattr:b3045913-62ac-433e-9211-ac683cdf6b5c .
arch:attribute-original a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "original" ;
  skos:exactMatch mbattr:4fd3b255-a7d7-4424-9a63-40fa543b601c .
arch:attribute-pre a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "pre" ;
  skos:exactMatch mbattr:288b973a-26ea-4880-8eca-45af4b8e8665 .
arch:attribute-re a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "re" ;
  skos:exactMatch mbattr:9b72452f-550e-4ace-93ed-fb8789cdc245 .
arch:attribute-solo a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "solo" ;
  skos:exactMatch mbattr:63daa0d3-9b63-4434-acff-4977c07808ca .
arch:attribute-translator a skos:Concept ; skos:inScheme arch:attributes ; skos:notation "translator" ;
  skos:exactMatch mbattr:25dfb08e-9b99-44db-b30c-1d6ec6747af8 .

arch:vocals a skos:ConceptScheme ; skos:notation "vocals" .
arch:vocal-alto_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "alto vocals" ;
  skos:exactMatch mbattr:9f63c4ba-b76f-40d5-9e99-2fb08bd4c286 .
arch:vocal-background_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "background vocals" ;
  skos:exactMatch mbattr:75052401-7340-4e5b-a71d-ea024a128849 .
arch:vocal-baritone_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "baritone vocals" ;
  skos:exactMatch mbattr:a40b43ed-2722-4b4a-98a5-478283cdf8df .
arch:vocal-bass_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "bass vocals" ;
  skos:exactMatch mbattr:1bfdb77e-f339-4e8e-9627-331ca9d9e920 .
arch:vocal-bass-baritone_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "bass-baritone vocals" ;
  skos:exactMatch mbattr:629763ee-3dc7-4225-b209-0ebb6d49bfab .
arch:vocal-choir_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "choir vocals" ;
  skos:exactMatch mbattr:43427f08-837b-46b8-bc77-483453af6a7b .
arch:vocal-contralto_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "contralto vocals" ;
  skos:exactMatch mbattr:80d94f2e-e38f-4561-add2-c866f083d276 .
arch:vocal-countertenor_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "countertenor vocals" ;
  skos:exactMatch mbattr:435a19f5-55dc-4a08-8c59-4257680b4217 .
arch:vocal-lead_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "lead vocals" ;
  skos:exactMatch mbattr:8e2a3255-87c2-4809-a174-98cb3704f1a5 .
arch:vocal-meane_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "meane vocals" ;
  skos:exactMatch mbattr:a31bdea5-7ef5-4a35-81c9-2abed9850d6d .
arch:vocal-mezzo-soprano_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "mezzo-soprano vocals" ;
  skos:exactMatch mbattr:f81325d7-593c-4197-b776-4f8a62c67a8e .
arch:vocal-other_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "other vocals" ;
  skos:exactMatch mbattr:c359be96-620a-435c-bd25-2eb0ce81a22e .
arch:vocal-soprano_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "soprano vocals" ;
  skos:exactMatch mbattr:e88f0be8-a07e-4c0d-bd06-e938eea4d5f6 .
arch:vocal-spoken_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "spoken vocals" ;
  skos:exactMatch mbattr:d3a36e62-a7c4-4eb9-839f-adfebe87ac12 .
arch:vocal-tenor_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "tenor vocals" ;
  skos:exactMatch mbattr:122c11da-651f-46cc-9118-c523a14afa1d .
arch:vocal-treble_vocals a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "treble vocals" ;
  skos:exactMatch mbattr:433631a2-68b7-49e6-90b4-5af19e26fc75 .
arch:vocal-whistling a skos:Concept ; skos:inScheme arch:vocals ; skos:notation "whistling" ;
  skos:exactMatch mbattr:ed220196-6250-456d-ab7b-465bee605b16 .
```

### D-18. Harmony as claims, keys, form, lead sheets, documents, notes, catalogues, bandmates, and exports (ADRs 0079 to 0089)

Specification 0.0.10 installs version 2 of `archive.claim/chord_stacks`,
`archive.claim/chord_sequence`, `archive.claim/meter_layers`, and
`archive.assessment/judgment`, and version 1 of `archive.claim/key_sequence`,
`archive.claim/key_regions`, `archive.claim/tuning`,
`archive.claim/span_annotation`, `archive.claim/form`,
`archive.claim/form_spans`, `archive.claim/lead_sheet`,
`archive.claim/document_source`, `archive.claim/timed_lyrics`,
`archive.claim/sensitivity`, `archive.claim/rights`, `archive.claim/notes`,
`archive.claim/melody`, `archive.claim/melody_spans`,
`archive.claim/tracklist`, `archive.claim/recording_audio`,
`archive.claim/catalogue_correspondence`, `archive.claim/work_origin`,
`archive.claim/sharing_consent`, and `archive.claim/export`, and version 2
of `archive.transition/publication_approval` and
`archive.transition/publication_withdrawal`. Every 0.0.9
contract bundle remains installed with its exact bytes, and every version 1
claim keeps its meaning under its own contract; a version 2 does not
supersede its version 1, and an importer that needs none of a version 2's
additions MAY write either. The layout is `archive/2`, and the core context
and the Entity kinds are those of 0.0.9, so the release is additive and a
0.0.9 archive adopts it by the specification upgrade of D-15. None of these
contracts is current by kind or asserter, and none stores which claim is
current (S-18).

Unless a contract says otherwise below, a timed claim follows D-17's rules
for chord stacks: it pins exactly one `source` Artifact revision and exactly
one `stream`, an audio stream version 1 Claim revision whose `artifact`
reference pins that Artifact object and revision; its payload's
`sample_rate` equals the stream's; no span ends after the stream's
`sample_count`; spans have integer `start_sample` and `end_sample` with
`0 <= start_sample < end_sample`; a `run` is a derivation run version 1 Claim
revision and a `document` an Artifact revision; `document_url` (D-17's URL
form), locators, and an `alignment` (D-17's object) appear only as stated; a
span's `end` is `asserted` (also when absent) or `held`, with a `hold_rule`
of 1 to 500 characters exactly when some end is held; and `weight` (exact
decimal text) appears on every reading exactly when the payload states a
`weight_scale` (1 to 200 characters). An untimed claim pins no audio and has
no `sample_rate` and no `alignment`; its subject is a `work`, a
`musical_work` Entity revision, a `document`, or both. `corrects` names the
claims a claim corrects, of the kinds each contract states; a correction
replaces nothing. A `part` (1 to 100 characters) names the one player a claim
describes; a claim without `part` describes the whole sound, and a mix claim
and a part claim that differ are a difference, never a conflict.

**Lineage.** `derived_from` names zero to eight exact Claim revisions the
asserter read the claim from, with or without a run; it maps to
`prov:wasDerivedFrom`, and no view replaces, hides, or ranks a claim because
another derives from it. On the musical contracts it MAY pin a Claim citing
chord stacks version 1 or 2, chord sequence version 1 or 2, meter layers
version 1 or 2, key sequence, key regions, tuning, form, form spans, notes,
melody, or melody spans. No revision MAY appear twice in `derived_from`, and
none in both `corrects` and `derived_from`.

**A stem cited by bank key.** A payload's optional `stem` is the closed object
`{extractor, key, output, sha256}`: `extractor` is a lowercase ASCII letter
followed by at most 62 lowercase letters, digits, and hyphens; `output` is the
same with underscores in place of hyphens; and `key` and `sha256` are
canonical SHA-256 digests. With the content digest of
the pinned `source` revision it names one file of a feature bank,
`sha256/<ab>/<cd>/<source hex>/bank/<extractor>/<key hex>/` output `output`,
whose bytes have the digest `sha256`. The citation says which stem the
asserter read; it is not evidence of the bytes, which may be deleted and made
again, and Door does not check the file. A claim with a `stem` MUST NOT pin an
`analysed` Artifact. The claim's samples are the source's.

**Chord stacks version 2** is version 1 (D-17) with these additions: an
optional `stem`; zero to eight `derived_from`; 1 to 100,000 spans;
`corrects` MAY pin chord stacks version 1 or 2; `sequence` MAY pin chord
sequence version 1 or 2; and a reading MAY be **bass-only**: an object of
`bass`, a pitch class, optional `root` (a pitch class), optional `text`, and
`weight` under the weight rule, and nothing else. A bass-only reading says
which pitch class is lowest and nothing above it; a stack with `"tones": []`
still says that only the bass sounds. **Chord sequence version 2** is version
1 with `derived_from`, the bass-only reading, and `corrects` of chord
sequence version 1 or 2; it has no samples and no stem, and keeps 10,000
entries.

**Meter layers version 2** is timed when it pins a `source`, and untimed
otherwise. A timed claim is version 1 (D-17) with an optional `stem`;
`derived_from`; zero to eight `corrects` of timed meter layers version 1 or
2; and at most one `meter`, an untimed meter layers version 2 Claim revision
an alignment placed on this recording, in which case it states `alignment`
and its bars MAY name `placed`, `{layer, bar}`, the 0-based position of the
untimed bar it was placed from (a repeated bar is placed once per pass). A
claim without `meter` MUST NOT have `placed`. An untimed claim is a Work's or
a document's written meter: it pins no `stream`, `analysed` Artifact, or
`meter`; MAY pin at most one `form`, a form version 1 Claim revision, at most
one `sequence`, a chord sequence version 1 or 2 Claim revision, at most one
`run`, zero to eight `corrects` of untimed meter layers version 2, and
`derived_from`; its payload holds `layers`, optional `modulations`, and
`document_url` with a document. An untimed layer holds 1 to 10,000 bars and
never beats; a bar has `numerator`, `denominator`, and optional `grouping`
as in D-17, an optional `locator` (`bar`, with a document), an optional
`section` (the 0-based position of a section of the pinned form), and
optional `chords`, 1 to 64 objects of `entry` (the 0-based position of an
entry of the pinned sequence that changes in this bar) and optional `at`,
the written offset from the downbeat. A section needs a pinned form and
chords a pinned sequence. Within a layer, sections, entries, and locator bars
never go back; within a bar, offsets strictly increase and are less than the
bar's length. Untimed modulations follow D-17's rules with the bars of a
layer adjacent by position.

A **written length** is `{count, unit}`: `count` notes of value 1/`unit`, an
integer `unit` from 1 to 1,024, so a dotted quarter is 3/8 and a triplet
eighth 1/12. A duration's `count` is at least 1; an offset's is at least 0.

**Key readings.** A key reading is a **key**: `collection`, 1 to 12 pitch
classes in strictly ascending order; optional `tonic`, a class of the
collection; optional `mode` (1 to 100 characters), only with a tonic; and
optional `spelling`, the asserter's name for each class in the collection's
order, each a letter `A` to `G` and at most two sharps (`#`) or flats (`b`)
that sounds that class; or **no key**, `no_key` `true`, a statement that the
passage has no tonal centre; or **text only**, the source's `text`. Every
form MAY carry `text` (1 to 200 characters) and the weight rule. A `mode` of
`ionian`, `dorian`, `phrygian`, `lydian`, `mixolydian`, `aeolian`, `locrian`,
`harmonic minor`, or `melodic minor` MUST name that mode's collection on the
tonic; `major` is `ionian`, and `minor` is any of `aeolian`, `harmonic
minor`, and `melodic minor`. Any other mode text is the asserter's and is not
checked. A region's `readings` are 1 to 16 distinct readings in the
asserter's order of preference.

**Key sequence** is untimed keys of a Work or a document. It MAY pin at most
one `run`, zero to eight `corrects` (key sequence version 1), `derived_from`,
and at most one anchor: a `chord_sequence`, a chord sequence version 1 or 2
Claim revision, or a `form`, a form version 1 Claim revision. Its payload has
`regions` (1 to 1,000) and optional `part`, `weight_scale`, and
`document_url`. A region has `readings`, an optional `section` (1 to 100
characters), an optional `locator` (`line`, at least 1, with a document,
never going backwards), and, exactly when the claim pins an anchor, `first`
and `last`, positions of the anchor's entries or sections as written, with
`first <= last` and each region's `first` after the previous region's
`last`. **Key regions** is one asserter's keys over one stream: a timed
claim with optional `part`, `weight_scale`, `hold_rule`, `alignment`,
`document_url`, and `stem`, and no `analysed` role. It MAY pin at most one
`tuning`, a tuning Claim revision whose `source` is this claim's `source`
revision; at most one `sequence`, a key sequence an alignment placed here
(then it states `alignment`, and a region MAY name `region`, the position of
the sequence region it was placed from, which needs the `sequence`); zero to
eight `corrects` (key regions version 1); and `derived_from`. `regions` holds
1 to 1,000 ordered, non-overlapping spans with `readings` and optional `end`
and `locator` (`bar`, with a document).

**Tuning** says where twelve-tone equal temperament lies on one stream. A
timed claim with no `analysed` role and no `alignment`, it MAY pin `run`,
`document`, zero to eight `corrects` (tuning version 1), and `derived_from`.
Its payload has `sample_rate`; `reference_cents`, the grid's offset from
A = 440 Hz as exact decimal text, at least -50 and less than 50; optional
`drift`, 1 to 10,000 ordered, non-overlapping spans with `cents` in the same
range, where the grid lies elsewhere; optional `confidence_scale` (1 to 200
characters), and then `confidence` (exact decimal text) on the payload and
every drift span, and otherwise none; optional `document_url` with a
document; and optional `stem`. **Pitch classes are relative to the grid in
force**: a chord's classes, a key's collection, and a note's pitch name the
nearest step of the drift span's grid within it and the reference's
elsewhere. An instrument tuned down or capoed is not a recording's tuning.

**Judgment version 2** is version 1 (D-13) with at most one scope in its
payload: `span`, `{start_sample, end_sample}` with `0 <= start_sample <
end_sample`, with `sample_rate`, which appears exactly with `span`;
`entries`, `{first, last}` with `0 <= first <= last`; or `reading`,
`{reading}` and exactly one of `span` or `entry`, each an integer of at
least 0. The judged Claim revision is timed when its payload states an
integer `sample_rate` and it pins exactly one `stream`. A span scope needs a
timed judged claim with the same `sample_rate`, and its span ends within the
judged claim's stream's `sample_count`. An entries scope's `last` is a
position of the judged claim's `entries`. A reading scope names a span (of a
timed claim's `spans`) or an entry that exists and a reading it has. A
version 2 `withdrawn` judgment's envelope `agent` MUST equal the judged
claim's: only the asserter takes a claim back. A version 2 judgment with no
scope judges the whole claim, as version 1 does.

**Span annotation** is a person's mark. Its envelope `agent` MUST be an
Archive object identifying a `person` Entity. It marks either samples of a
recording, pinning exactly one `source` and one `stream` with `sample_rate`
and `span` (as a judgment's) in its payload, or entries of a chord sequence,
pinning exactly one `sequence` (chord sequence version 1 or 2) and at most one
`work`, with `entries` (as a judgment's) whose `last` is a position of the
sequence's entries; never both. A `work` it pins MUST be the Work revision
that sequence pins. Its payload's `kind` is `unsure`, `interesting`, or
`note`, and `text` (1 to 2,000 characters, the asserter's own words) appears
exactly when the kind is `note`. It MAY pin zero to eight `about` Claim
revisions, distinct and none its own stream or sequence; each MUST pin, under
some role, the marked recording's `source` revision, or the sequence's `work`
or `document` revision.

**Form** is untimed sections of a Work or a document. It MAY pin at most one
`run` and zero to eight distinct `corrects` (form version 1). Its payload has
`sections`, 1 to 1,000 objects of `label` (1 to 100 characters, the source's
own), optional `kind` (`intro`, `verse`, `pre_chorus`, `chorus`,
`post_chorus`, `refrain`, `bridge`, `interlude`, `instrumental`, `solo`,
`break`, `vamp`, `head`, `outro`, `coda`, or `tag`), and optional `locator`
(exactly one of `line` or `bar`, with a document; located sections use one
kind of locator and never go backwards); optional `repeats`, 0 to 1,000 of
`first`, `last`, `times` (2 to 999), and optional `endings`; optional
`jumps`, 0 to 8; and optional `document_url`. Repeats follow D-17's rules for
a chord sequence's repeats over the sections. A repeat's `endings` are 2 to
16 objects of `first`, `last`, and `passes` (distinct ascending pass numbers
from 1 to `times`): they follow a non-empty body one after another, the last
ending at the repeat's `last`, and every pass has exactly one ending; a
repeat inside one with endings lies within its body or one ending. A jump is
a D.C. or D.S. at the end of section `after`: `kind` `da_capo` or
`dal_segno` (which needs `to`, the segno's section, at most `after`), `then`
`end`, `fine` (which needs `fine`, a section at or after where play resumes),
or `coda` (which needs `coda_from`, at or after where play resumes and before
the last section, and `coda`, after both `after` and `coda_from`), and
optional `repeats_on_return`. Where play resumes and the coda are never
strictly inside a repeat, and jumps are at distinct sections in written
order. **Form spans** is a timed claim of sections on a recording: optional
`document_url` and `alignment`; at most one `form` it places (then it states
`alignment`) and zero to eight distinct `corrects` (form spans version 1);
`spans`, 1 to 10,000 ordered, non-overlapping spans, each with a `label` or
a `section` (the position of a section of the pinned form, which needs the
form) or both, an optional `kind`, and an optional `locator` as a section's.

**Lead sheet** is a person's or a band's chosen version of a Work. Its
envelope `agent` MUST identify a `person` or `organisation` Entity, never
software. It pins exactly one `work`; at most one `source` Artifact, the
recording its timed inputs count; at most one `recording` (a `recording`
Entity, an audio selection or song occurrence version 2 Claim, or an
Artifact), which needs the `source`; at least one of `chords` and `melody`;
and, as chosen inputs, `chords` (0 to 16, chord stacks or chord sequence
version 1 or 2), `meter` (0 to 4, meter layers version 1 or 2), `form` (0 to
4, form or form spans), `key` (0 to 4, key sequence or key regions), `lyrics`
(at most one Artifact, the lyric document), `lyric_timing` (0 to 4, timed
lyrics, which need `lyrics` and place exactly that document), `melody` (0 to
4, melody or melody spans), and `credits` (0 to 32, work-scope contribution
Claims about the same Work object); with `document` (0 to 8 Artifacts a
part's strings were read from), `derived_from` (0 to 8 lead sheets of the
same Work object), `band` (at most one `organisation`), and `adopted_by` (0
to 16 band membership Claims). No revision is pinned twice. An input that
pins a `source` MUST pin the lead sheet's `source` revision, and one that
pins a `work` MUST name the lead sheet's Work object. A person's lead sheet
pins no `band` and no `adopted_by`; a band's pins the band, which is its
`agent`, and the memberships of that band of the members who adopted it, no
member twice. Its payload MAY hold `label` (1 to 200 characters) and `parts`,
1 to 16 objects of distinct `name` (1 to 100 characters), optional
`instrument` (a MusicBrainz instrument URI), optional `strings` (1 to 12 MIDI
notes, in the tablature's order), optional `capo` (1 to 24, with `strings`),
and optional `document` (the position of one of the `document` references,
with `strings`). A later revision of a lead sheet keeps its agent, its Work
object, and its `source` revision.

**Document source** says what a document Artifact is and where it came from.
It pins exactly one `document` Artifact, zero to eight `derived_from`
Artifacts (the content it was made from), zero to sixteen distinct `work`
Entities of kind `musical_work`, and at most one `run`; no object is both the
document and among what it was made from. Its payload has `media_type`, one of
`text/plain`, `text/markdown`, `application/json`, `application/pdf`,
`application/rtf`, the Word and OpenDocument text types, the two MusicXML
types, `application/x-guitar-pro`, `audio/midi`, `image/png`, and
`image/jpeg`; optional `kinds`, distinct and ascending, from `booklet`,
`catalogue_response`, `chords`, `licence`, `lyrics`, `notes`, `other`,
`score`, `tablature`, and `transcript` (a transcript pins its `run` and at
least one `derived_from`); optional `url` (D-17's URL form), which needs
`retrieved_at`, and which, for a Google Docs document, MUST be its canonical
`https://docs.google.com/document/d/<id>`; optional `retrieved_at` (as
D-17's); and optional `filename` (1 to 255 characters, a name: not `.` or
`..`, and with no `/`, `\`, or NUL).

**Timed lyrics** places lines and words of one lyric document on one stream
and holds no word of the lyric. It pins exactly one `document` Artifact, one
`source` of another object, one `stream`, at most one `work`, at most one
`run`, and zero to eight distinct `corrects` (timed lyrics version 1). Its
payload has `sample_rate`, `scheme` (`text` or `lrc`), `alignment`, `lines`
(1 to 10,000), optional `hold_rule`, and optional `confidence_scale`. A line
has `line` (its number in the document, from 1), a span, an optional `end`,
optional `words` (1 to 200), and `confidence` exactly with a scale. Lines are
in order of start and may overlap. A word has `word` (its number in the
line, from 1, strictly increasing), a span within its line's, after the word
before it, an optional `end`, `confidence` exactly with a scale, and optional
`syllables` (1 to 32). A syllable has `syllable` (its number in the word,
from 1, strictly increasing), `start_char` and `end_char` (Unicode code
point offsets into the word, from 0, `start_char < end_char`), a span within
its word's and at or after the syllable before it, character ranges at or
after the syllable before it, `confidence` exactly with a scale, and
optional `carried_by`: `notes`, the revision of one pinned notes Claim, and
`onsets`, 1 to 64 strictly increasing `onset_sample` values of notes in it
(several are a melisma; of notes sharing an onset, the highest is meant). A
syllable has no `end`: a held word's hold is its last syllable's. The claim
MAY pin 0 to 8 distinct `notes` (notes version 1 Claim revisions about the
same `source` revision), and the revisions the syllables' `carried_by` name
MUST be exactly those pinned. A machine's timed lyrics come from aligning
the document's own words to the audio, never from transcribed text, under
a `run`. The document is UTF-8; a line ends at LF, CR LF, or a lone CR; a word is a
maximal run of characters without the Unicode `White_Space` property; under
`lrc`, time tags are not words and line numbers count every line of the file.
Door does not read content, so whether a numbered line or word exists is not
Door's to check.

**Sensitivity** is a person's limit on how far objects may reach (S-32). Its
envelope `agent` MUST identify a `person` Entity. It pins 1 to 1,000
`subject` references of any shape, naming different objects. Its payload has
`label`, a Traffic Light Protocol 2.0 label (`RED`, `AMBER`, `GREEN`, or
`CLEAR`), and optional `reason` (1 to 2,000 characters). A limit holds for
everything that pins its subject, transitively, so a transcript of a `RED`
recording is `RED`.

**Rights** states one rights statement about 1 to 1,000 `subject` references
(Artifacts, or Entities of kind `musical_work`, `recording`, or `release`),
with 0 to 8 `holder` references (`person` or `organisation` Entities), at
most one `licensee` (a `person`), at most one `document`, and at most one
`run`; subjects and holders name different objects. Its payload has
`statement`, a RightsStatements.org statement identifier (the scheme at the
end of this section) or `personal-use-licence`, which needs exactly one
`licensee` (any other statement has none); optional `jurisdiction` (an ISO
3166-1 alpha-2 code); optional `from` and `until` (partial dates as D-17's,
`until` not before `from`); optional `text` (1 to 2,000 characters); and
`document_url` and `retrieved_at` only with a document. Rights never limit
the circle: whatever its statement, anything MAY be disclosed to the
operator's circle, subject to sensitivity. Beyond the circle, a rights claim
permitting it is needed (S-30), and among several the strictest applies; a
`personal-use-licence` reaches the circle and nothing beyond it.

**Notes** is one part's notes of one recording: a timed claim with `part`
(required), optional `stem`, optional `range` (a span: the claim asserts
silence between notes inside it and nothing outside; without it, the whole
stream), optional `unpitched` (`true`), optional `confidence_scale` (then
every note has `confidence`, and otherwise none does), optional `strings`
and `capo` (as a lead sheet part's, only with a document), optional
`document_url`, and `alignment` exactly with a document. It pins at most one
`analysed` Artifact (never with a `stem`), at most one `tuning` (a tuning
claim of the same `source` revision; never on an unpitched part), zero to
eight `corrects` (notes version 1), `derived_from`, and at most one
`contribution` (contribution version 1). `notes` holds 1 to 100,000 notes in
strictly ascending order of `onset_sample` and then `pitch`, each with
integer `onset_sample` within the range, `offset_sample` after it and within
the stream, `pitch` (a MIDI note number, 0 to 127, the nearest step of the
grid), optional `cents` (from that step, in [-50, 50), only with a pinned
tuning), optional `velocity` (1 to 127), an optional `locator` (`bar`
and optional `at` and `duration` written lengths, with a document), and
optional `articulation`. A longer part is split into claims by range. An
`articulation` is 1 to 6 distinct names from `staccato`, `staccatissimo`,
`tenuto`, `accent`, `marcato` (MusicXML's `strong-accent`), and `fermata`,
as a document or a person wrote them; a claim that pins a `run` and no
`document` MUST NOT carry any. Optional `bass` (`true`) says the part is the
bass line; a claim whose `stem` has `output` `bass` MUST say it. How sounded
notes are written on a beat grid is a reading, never stored in the claim.

**Melody** is a Work's or a document's tune, untimed: optional `run`, at most
one `meter` (an untimed meter layers version 2 Claim revision whose bars it
is laid on), zero to eight `corrects` (melody version 1), and
`derived_from`. Its payload has `notes`, 1 to 10,000 items in written order,
each a `pitch` (0 to 127) or a `rest` (`true`) with a `duration` and an
optional `locator` (`bar`, with a document), at least one a pitch, and a
pitch with optional `articulation` as a note's (none when the claim pins a
`run` and no `document`); optional
`part`; optional `document_url`; and optional `start`, `{layer, bar, at?}`,
only with a `meter`. **Melody spans** says which part carries a recording's
tune: a timed claim pinning 1 to 16 distinct `notes` Claims about the same
`source` revision, at most one `run`, zero to eight `corrects` (melody spans
version 1), and `derived_from`; its payload has only `sample_rate` and
`spans`, 1 to 10,000 ordered, non-overlapping spans, each naming in `notes`
the revision of one pinned notes claim, with optional `select` (`all`,
`highest`, or `lowest`). Every pinned notes claim is named by some span. A
span MAY name a `bass` notes claim; a reader's tune of a recording leaves
out such a span when the melody spans claim's `agent` is not a person, and
says what it left out.

**Tracklist** says what is on a release. It pins exactly one `release`
Entity and one `track`, a `recording` Entity, per listed track, in order. Its
payload has `media`, 1 to 200 objects of `position` (from 1), `tracks` (1 to
999), optional `format`, and optional `title`; a track has `position` (0 to
999; 0 is a hidden pregap track, first on its medium) and optional `number`,
`title`, `artist_text`, and `length_ms`; at most 4,000 tracks in all. Media
and tracks are in strictly ascending position, and unless the payload says
`partial` (`true`) media are numbered 1, 2, … and a medium's tracks from 0 or 1
with no gap. It MAY carry D-17's asserter fields with their document rules,
`text` not required. **Recording audio** says archived audio is a recording:
exactly one `recording` Entity and one `audio` (an Artifact, or an audio
selection or song occurrence version 2 Claim), with `extent` `whole` or
`part`; it MAY pin a `tracklist`, and then states `medium` and `track`, a
position that tracklist lists whose `track` reference names this claim's
recording object. **Catalogue correspondence** maps exactly one of an
`artist` (`person` or `organisation`), a `release`, or a `recording` Entity
to one `external` entry by `mapping`: a MusicBrainz artist, release, or
recording, or a Discogs artist or release, by `skos:exactMatch` or
`skos:closeMatch`, from the subject of the same kind; and a MusicBrainz
release group or a Discogs master by `skos:broadMatch`, from a release. Each
`external` is the canonical URI, `https://musicbrainz.org/<type>/<MBID>` or
`https://www.discogs.com/<type>/<id>`. Both carry the asserter fields as a
tracklist does.

**Work origin** answers whose a musical Work is. Its envelope `agent` MUST
identify a `person` Entity. It pins exactly one `work`, 0 to 32 distinct
`writer` `person` Entities, and at most one `band` (`organisation`). Its
payload is `{origin}`: `co_written` names at least two writers, the active
operator's person among them; `own` names no writer or only the operator, and
no band; `others` never names the operator and names no band. **Sharing
consent** is a co-writer's answer for one destination: its `agent` MUST be a
writer that its pinned `origin` (one work origin Claim revision whose origin
is `co_written`) names, and never the active operator's person; it pins 1 to
64 distinct `covers` Claim revisions; its payload has `destination` (1 to 200
characters), `decision` (`approve` or `refuse`), and optional `reason`. A
consent authorizes nothing by itself (S-30). A record of this release or a later one that
cites a member contract (ADR 0088: judgment versions 1 and 2, span
annotation, chord stacks, chord sequence, and meter layers versions 1 and 2,
key regions, key sequence, tuning, form, form spans, lead sheet, timed
lyrics, notes, melody, melody spans, contribution, band membership,
selection kind, selection title, performance/work version 3, and sharing
consent) and supersedes a revision MUST keep that revision's `agent`; Door
checks this beside the bundles, since the earlier bundles' bytes are fixed.

**Export** records a file rendered from exact revisions that was sent to
someone. It pins exactly one `artifact`, the exported file, 1 to 64
`rendered` Claim revisions, and 0 to 16 `joined` Artifact revisions, no
revision twice. Its payload has `format` (`pdf`, `musicxml`, `lilypond`, or
`jams`); `media_type`, fixed by the format (`application/pdf`,
`application/vnd.recordare.musicxml+xml`, `text/x-lilypond`, or
`application/json`); `exporter`, `{id, version}` (`id` as a stem's
`extractor`, `version` an ASCII letter or digit followed by at most 39
letters, digits, and dots);
`key`; optional `options`, at most 32 named scalars (names as a stem's
`output`; values booleans, integers of magnitude below 2^53, or text of 1 to
200 characters); optional `software` (1 to 32 `{name, version}`); and
optional `losses` (1 to 32 distinct texts of 1 to 200 characters). `key`
MUST be `sha256:` and the lowercase hexadecimal SHA-256 of the RFC 8785
canonical JSON of `{format, exporter: {id, version}, options, rendered,
joined}`, where `options` is the payload's (or `{}`) and `rendered` and
`joined` are the pinned revisions in reference order, each `[id,
revision_id]`.

**Publication beyond the circle.** A publication approval version 2 is
version 1 (D-13) with a required `class`, the exposure class of its
destination (`private`, `circle`, `service`, `open`, or `distribution`),
which is the furthest it reaches. When its `decision` is `approve` and its
class is `service`, `open`, or `distribution`, Door MUST refuse it unless,
for every `covers` revision, on the UTC date of the transition: the
object's furthest class by sensitivity, over everything it pins
transitively and every revision of each, reaches the class (S-32); every
Artifact and every `musical_work`, `recording`, and `release` Entity in that
closure has a rights claim permitting the class, the strictest claim about
each object winning (S-30); and every co-written Work the covered revisions
touch has, from each of its writers other than the active operator, a head
sharing consent whose `decision` is `approve`, whose `destination` is the
approval's exactly, and whose `covers` include every covered revision. A
rights statement permits: `InC` or `InC-*` with the active operator among
its holders, and `NoC-US`, distribution; `NoC-NC`, open; a
`personal-use-licence`, the circle; any other statement, or any statement
outside its `from` and `until`, service. Door names each object, label,
statement, writer, and revision that blocks the approval. No override past
a sensitivity limit is defined, so Door makes none. A publication approval
version 1 states no class and authorizes disclosure no further than the
circle. A publication withdrawal version 2 is version 1 whose `withdraws`
pins a publication approval of version 1 or 2; no approval is withdrawn
twice by withdrawals of either version. A reader MUST check again, when
publishing, that the approval is the head, not withdrawn, and still passes
these checks on that day.

**Terms.** Each fragment maps its payload fields and roles; `derived_from`
maps to `prov:wasDerivedFrom`, `run` to `prov:wasGeneratedBy`, `document` to
`dcterms:source` (except where the document is the claim's subject), and
`stream` to `arch:measured_stream`, as in D-15 and D-17. Payload fields that
share a core term's name (a section's or a mark's `kind`, a part's `name`, a
sensitivity's `label`, an export's `artifact` and `software`) are not mapped.

**JSON Schema keywords.** The evaluator also supports `propertyNames` (a
schema every property name of an object, as a string, MUST satisfy) and
`minProperties` and `maxProperties` (non-negative integers bounding an
object's number of properties). Each applies only to an object instance.

**Profile rules.** `x-archive-pitch-stacks` applies to every reading with a
`bass` and `tones`; a bass-only reading has nothing for it to check, and no
earlier bundle's meaning changes. The release adds the rules below. Each is
closed and root-only, names distinct fields, and an unknown or malformed rule
MUST be refused when the bundle loads. A Door that installs a bundle declaring
a rule it does not enforce MUST refuse every record citing that bundle, and a
generic schema success MUST NOT bypass a rule. A "timed form" means a record
that pins a reference in the rule's role; "the active operator" is the person
D-13 names.

- `x-archive-distinct-revisions` names `roles`: no revision MAY appear twice
  among the references in those roles.
- `x-archive-ordered-fields` is a list of `{path, low, high, strict}`: on
  every object the path reaches, the integer `low` MUST be less than (`strict`
  true) or at most `high`.
- `x-archive-ordered-ranges` names a payload array `payload` and integer
  fields `first` and `last`: on each element with both, `first <= last`, and
  each such element's `first` is after the previous one's `last`.
- `x-archive-ascending-tuples` is a list of `{payload, fields, strict}`: in
  every array the path `payload` reaches, elements are in ascending order of
  the tuple of their integer `fields`, strictly when `strict` is true.
- `x-archive-contained-spans` names an `outer` array, the `inner` array each
  of its elements holds, and integer `start` and `end`: every inner span lies
  within its outer element's.
- `x-archive-distinct-values` names `paths`: the values reached by each path
  are distinct.
- `x-archive-file-names` names payload `fields` whose string values MUST NOT
  be `.` or `..` or contain `/`, `\`, or NUL.
- `x-archive-spellings` names a `payload` path to readings, their `classes`
  array, and their `names` array: where both are present they have equal
  length and each name sounds the class at its position.
- `x-archive-when-pinned` names a `role` and two maps of earlier profile
  rules, `then` and `else`: the `then` rules apply exactly when the record
  pins a reference in `role`, and the `else` rules otherwise. A rule named in
  either map MUST NOT also appear at the root.
- `x-archive-written-bars` names `layers`, their `bars`, and `modulations`,
  and checks an untimed meter layers payload as stated above.
- `x-archive-target-form` names a role `by` and a map from roles to `without`
  or `same`: a Claim pinned in a `without` role pins no reference in `by`; one
  pinned in a `same` role pins one exactly when this record does.
- `x-archive-pinned-subjects` is a list of `{roles, subject, equals, match}`:
  for each Claim pinned in any of `roles` and each reference it has in
  `subject`, this record MUST pin a reference in `equals` naming the same
  object (`match` `object`) or the same object and revision (`revision`).
- `x-archive-target-values` is a list of `{role, field, values}`: each Claim
  pinned in `role` has payload `field` with one of `values`.
- `x-archive-target-span` names a `role` whose one Claim has integer payload
  fields `rate` and `count`, and this payload's object `span` with integer
  `start` and `end`: when the record pins the role, its `rate` equals the
  Claim's and `end` is at most `count`.
- `x-archive-within-range` names a `role` whose one Claim has an integer
  `count`, a payload object `range` with integer `start` and `end`, and a
  payload array `items` with integer `field`: `range.end` is at most `count`,
  and with a range every `field` is at least `start` and less than `end`.
- `x-archive-ref-index` names a `path` and a `role`: every integer the path
  reaches is a position among the record's references in `role`.
- `x-archive-ref-per-item` names a `path` and a `role`: the items the path
  reaches, in order, number exactly the references in `role`.
- `x-archive-named-refs` names a `path` and a `role`: the revision
  identifiers the path reaches are exactly the revisions pinned in `role`.
- `x-archive-agent` names Entity `kinds`: the envelope `agent` is an Archive
  object whose head Entity revision has one of those kinds.
- `x-archive-supersession` names `agent` (`true`), `objects`, and
  `revisions`: a record that supersedes a revision keeps its agent, names the
  same object in each of `objects`, and pins the same revision in each of
  `revisions`.
- `x-archive-asserter-only` names a payload `field`, a `value`, and a `role`:
  when the field has that value, the envelope `agent` equals the agent of the
  record pinned in `role`.
- `x-archive-judged-scope` names the `role` of the judged Claim and checks a
  judgment version 2's scope against it, as stated above.
- `x-archive-mark-about` names the `about` role and checks a span
  annotation's `about` references and `work`, as stated above.
- `x-archive-form-structure` names `sections`, `repeats`, and `jumps`, and
  checks a form's locators, endings, and jumps, as stated above.
- `x-archive-adoption` names the `band` and `adopted_by` roles and checks a
  lead sheet's asserter and adoption, as stated above.
- `x-archive-tracklist-positions` names `media`, `tracks`, `position`, and
  `partial`, and checks a tracklist's positions, as stated above.
- `x-archive-tracklist-entry` names the `tracklist` role and the `medium`,
  `track`, and `recording` it compares, as stated above.
- `x-archive-operator-writers` names the `field` holding the origin and the
  `writer` role, and checks a work origin's writers against the active
  operator, as stated above.
- `x-archive-consenting-writer` names the `role` of the pinned origin and its
  `writers` role: the envelope `agent` is one of those writers and is not the
  active operator's person.
- `x-archive-export-key` names the payload `key` and the `rendered` and
  `joined` roles, and recomputes the key as stated above.
- `x-archive-sequential-spans` and `x-archive-contained-spans` MAY each be a
  nonempty list of rule objects, each checked as if it stood alone. Their
  `within` and `outer` MAY be a path, a nonempty list of payload field
  names each reaching an array inside the elements of the one before it;
  the rule then applies within each element the whole path reaches.
- `x-archive-beyond-circle` names the payload `class`, the classes it
  checks (`beyond`), the payload `decision` and its `approve` value, the
  payload `destination`, and the `covers` role, and checks a publication
  approval as stated above. Unlike the other rules it reads the snapshot's
  sensitivity, rights, work origin, sharing consent, band membership, and
  performance/work claims.

A reference target of chord stacks `sequence`, a lead sheet's inputs, and the
other roles that accept several contracts use D-17's `any`. Whether a
section or entry index is within a pinned form or sequence, a placed bar's
time signature, a tune that fits its meter, and a melody span within its
notes claim's range read a pinned claim's payload in ways no rule here
states; they are World's checks, and Door does not refuse them. So are a
syllable's character offsets within its word and each `carried_by` onset
being a note of the named claim.

**The rights statements.** This is the vocabulary a rights claim's
`statement` draws from, as SKOS. Each RightsStatements.org statement is a
concept whose notation is its identifier and which is `skos:exactMatch` the
statement's URI; the personal-use licence is Archive's own concept.

```turtle
@prefix arch: <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .

arch:rights a skos:ConceptScheme ; skos:notation "rights" .
arch:rights-InC a skos:Concept ; skos:inScheme arch:rights ; skos:notation "InC" ;
  skos:exactMatch <http://rightsstatements.org/vocab/InC/1.0/> .
arch:rights-InC-OW-EU a skos:Concept ; skos:inScheme arch:rights ; skos:notation "InC-OW-EU" ;
  skos:exactMatch <http://rightsstatements.org/vocab/InC-OW-EU/1.0/> .
arch:rights-InC-EDU a skos:Concept ; skos:inScheme arch:rights ; skos:notation "InC-EDU" ;
  skos:exactMatch <http://rightsstatements.org/vocab/InC-EDU/1.0/> .
arch:rights-InC-NC a skos:Concept ; skos:inScheme arch:rights ; skos:notation "InC-NC" ;
  skos:exactMatch <http://rightsstatements.org/vocab/InC-NC/1.0/> .
arch:rights-InC-RUU a skos:Concept ; skos:inScheme arch:rights ; skos:notation "InC-RUU" ;
  skos:exactMatch <http://rightsstatements.org/vocab/InC-RUU/1.0/> .
arch:rights-NoC-CR a skos:Concept ; skos:inScheme arch:rights ; skos:notation "NoC-CR" ;
  skos:exactMatch <http://rightsstatements.org/vocab/NoC-CR/1.0/> .
arch:rights-NoC-NC a skos:Concept ; skos:inScheme arch:rights ; skos:notation "NoC-NC" ;
  skos:exactMatch <http://rightsstatements.org/vocab/NoC-NC/1.0/> .
arch:rights-NoC-OKLR a skos:Concept ; skos:inScheme arch:rights ; skos:notation "NoC-OKLR" ;
  skos:exactMatch <http://rightsstatements.org/vocab/NoC-OKLR/1.0/> .
arch:rights-NoC-US a skos:Concept ; skos:inScheme arch:rights ; skos:notation "NoC-US" ;
  skos:exactMatch <http://rightsstatements.org/vocab/NoC-US/1.0/> .
arch:rights-CNE a skos:Concept ; skos:inScheme arch:rights ; skos:notation "CNE" ;
  skos:exactMatch <http://rightsstatements.org/vocab/CNE/1.0/> .
arch:rights-UND a skos:Concept ; skos:inScheme arch:rights ; skos:notation "UND" ;
  skos:exactMatch <http://rightsstatements.org/vocab/UND/1.0/> .
arch:rights-NKC a skos:Concept ; skos:inScheme arch:rights ; skos:notation "NKC" ;
  skos:exactMatch <http://rightsstatements.org/vocab/NKC/1.0/> .
arch:rights-personal-use-licence a skos:Concept ; skos:inScheme arch:rights ;
  skos:notation "personal-use-licence" .
```

## 7. Not yet defined

This specification does not yet define, and implementations MUST NOT pretend it
does:

- the grammar of the policy language and its test fixtures;
- the rule for "current" in each claim kind;
- expunging records and their history, and erasing information derived from
  destroyed content;
- digest algorithm migration;
- migration of an archive between specification versions, other than the
  additive upgrade of D-15.

## 8. Versioning

The version follows `MAJOR.MINOR.PATCH`. Until 1.0.0, any release may change the
requirements. After 1.0.0, a change that makes a previously conforming archive
non-conforming requires a MAJOR increment.
