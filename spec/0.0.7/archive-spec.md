# Archive Specification

Version: 0.0.7
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
defined only by the rule in the claim's contract, and every materialisation of it
MUST derive from that rule.

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
block it.

**S-31.** A withdrawal from an irreversible destination MUST be recorded as
requested, with known outside copies reported.

**S-32.** Content MUST NOT be disclosed except to a destination that policy
grants for it; a fact that cannot be established MUST NOT widen disclosure.
Every disclosure MUST be recorded with its destination, the content, and the
policy revision that allowed it, whether or not any result is kept, and MUST
be recorded before any content is sent; a send that may have begun counts as
received. A processor
that received content MUST be reported as a possible external survivor when
that content is destroyed. A sensitivity claim MUST cap disclosure: RED at
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
bundles (D-15).

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
asserting person, model, or process. `at` MUST be Door's RFC 3339 UTC recording
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
earlier version MUST NOT accept it. Specifications 0.0.6 and 0.0.7 keep the
0.0.5 kinds and core context unchanged. This Archive-owned term does not establish an RDF
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
contracts, enrichment contracts other than those of D-15, and current rules
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
both remains installed with its exact bytes. A new match MUST cite version 2:
Door checks a version 1 match only structurally, as before, and does not
refuse one whose `run` pins any Claim. A match never establishes a Work.

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
