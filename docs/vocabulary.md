# Vocabulary

These words have fixed meanings in this project. Use them exactly. Changing what
a word means is an architectural change and needs a
[decision record](decisions/README.md).

## The concepts

| Concept | Verb | Responsibility |
|---|---|---|
| Archive | preserve | Durable evidence |
| Door | admit / transition | Sole authority for archival meaning |
| World | relate / query | Assemble evidence into a broader referential universe |
| Enrichment | derive | Machine/computational derivation without source mutation |
| Annotation | assert | Human-authored claims |
| Review | assess / adjudicate | Judge claims and settle between competing ones |
| Policy | declare | Human-authorized desired archival state |
| Plan / apply | reconcile | Compute the difference from policy, then act on it |
| Spool | distribute | External domain-agnostic work protocol |

These are meanings, not services or programs. One program may do several of
them, and one of them may span several programs. What must not happen is one
quietly taking over another's job.

**Archive** keeps evidence for decades and owns what it knows.

**Door** is the only authority for archival meaning. To *admit* is to bring
something new into the archive; to *transition* is to change archival state
(record a claim or assessment, revise policy, publish, supersede, retire,
destroy, hand over to a new operator). Door checks validity, not desirability,
and it does not move bytes. ([ADR 0002](decisions/0002-one-door.md))

**World** relates the archive's evidence to the outside world and answers
questions across it. It is a projection: rebuildable, never the owner.
([ADR 0011](decisions/0011-world.md))

**Enrichment** derives new evidence from existing evidence without changing its
inputs: a transcript, a loudness measure, a format identification.

**Annotation** is a person asserting a claim: "this recording is Paper Lanterns".

**Review** judges claims. To *assess* is to judge one claim; to *adjudicate* is
to settle between competing claims. Review need not be human.

**Policy** is the operator's declaration of what the archive should hold,
written in a small rule language. It is different from **configuration**, which
tells a program how to run (`b2_bucket = "..."`).
([ADR 0007](decisions/0007-policy.md))

**Plan / apply**: *plan* works out what should change to meet policy, and why;
*apply* carries it out. ([ADR 0008](decisions/0008-plan-and-apply.md))

**Spool** is a separate project, in its own repository, that keeps durable work:
a task is put on a spool, leased by a worker, then acknowledged with a result
or failed. Archive uses it; it knows nothing about Archive. Spool's *reclaim*
takes back a stalled lease and is unrelated to a reclaim verdict.

## Three meanings of "archive"

- **Archive**, capitalised: the system and its contract.
- **an archive**: an actual collection of archival data on disk. It lives outside
  this repository.
- **this repository**: the source code and specification. It never holds
  personal archival data.

## Records

How evidence is stored. ([ADR 0003](decisions/0003-record-model.md))

**Record**: one immutable revision of something, in one of four shapes:

- **artifact**: an object whose bytes the archive keeps;
- **entity**: a durable subject the operator asserts, such as a song, a person,
  or a book on the shelf;
- **claim**: a typed, versioned statement about other things;
- **assessment**: a judgment of an exact claim revision.

**Object**: the enduring thing records describe, with a stable identifier.
**Content**: bytes, identified by their hash; several objects may share the same
content. **Location**: where content currently sits; moving it changes no
record. **Occurrence**: one arrival of content; the same WAV imported twice is
two occurrences of one content.

**Revision**: one recorded state of an object. **Supersede**: to replace a
revision explicitly. **Reference**: a typed link to another object. **Pin**: a
reference to an exact revision.

**`by`** and **`agent`**: every record names the program that wrote it (`by`)
and the person, model, or process that asserted it (`agent`).

**Contract**: the versioned definition of a claim kind's payload.
**Current**: the claim that currently counts for a question, computed from
claims and assessments by the contract's rule, never stored.

## Evidence and state

**Evidence**: anything whose loss would lose information.
**Regenerable state**: anything that can be rebuilt from evidence, however
expensively. **Operational state**: bookkeeping a program keeps to do its job,
such as routine verification results. See [state-model.md](state-model.md).

**Provenance**: where a claim or transition came from: who, how, from what, and
under which policy.

**Evidentiary authority**: how strongly a claim should be believed.
**Governance authority**: who may cause a consequence; the operator's.
([ADR 0005](decisions/0005-claims-review-and-authority.md))

**Operator**: the person with governance authority over an archive; one at a
time. **Succession**: the recorded handover to a new operator.

**Transition**: a change to archival state that Door records, as a claim of a
transition kind.
**Consequential transition**: one that someone may later need explained ("why
is this only on cold storage?", "why is this gone?").

**Promotion**: bringing a fact from World into the archive as a claim.

**Quiet history**: the rule that history is written only when something
meaningful happened.

**Feature**: a measurement of content that an enrichment tool makes for other
tools to read, such as a pitch-class profile every 93 ms or a separated stem;
named `<extractor>/<output>`. A feature is not a claim and is never admitted.
It is one of five kinds: a *series* (rows of numbers on a time grid), a
*scalar*, *events*, *spans* (time ranges), or a *file*.
**Feature bank**: the regenerable store of features, filed by content digest
and keyed by everything that decides them; tools *read* it. **Extractor**: a
program in any language, with a manifest, that *extracts* features into the
bank under the extractor contract. (Proposed:
[ADR 0073](decisions/0073-feature-bank.md),
[ADR 0074](decisions/0074-extractor-contract.md).)

## Deletion

([ADR 0004](decisions/0004-deletion.md))

**Retire**: take an object out of the collection; its records stay.
**Destroy**: remove content from the archive's custody and from every copy it
controls. **External survivor**: a known copy the archive cannot remove.
**Information erasure**: removing what derived evidence reveals about destroyed
content; not yet designed. **Expunge**: erase a record and its history; rare and
manual.

## Copies

([ADR 0009](decisions/0009-copies-and-custody.md))

**Failure domain**: places likely to fail together: one disk, one house, one
provider account.

**Managed copy**: a copy git-annex controls. **Observed copy**: an outside copy
the archive can verify but not control. **Projection**: the same bytes seen
again, such as a symlink. **Source**: where content came from before admission,
such as a recorder card.

**Reclaim verdict**: the archive's statement that a source is safe to clear.

**Staging host**: a machine that holds new content in its own git-annex
repository and proposes it to the Door host.

## Publication and disclosure

([ADR 0010](decisions/0010-publication.md),
[ADR 0016](decisions/0016-disclosure.md))

**Disclose**: let content leave the operator's custody readably. **Publish**: disclose so
that others can see. **Processor**: a destination that receives content to
compute on it and shows it to no one else, such as a hosted model.

**Destination**: somewhere content is published or sent. **Exposure class**:
how exposed a destination is: private, circle, service, open, or
distribution.
**Sensitivity**: a claim a person makes about an object, in Traffic Light
Protocol labels (RED, AMBER, GREEN, CLEAR).

**Origin**: where content came from (own capture, commercial release, ...).
**Rights**: what may legally be done with it, recorded with terms and dates.

## Collections and meaning

**Physical item**: a thing in the collection whose bytes the archive cannot
keep, such as a record, a book, or a notebook. It is admitted and retired like a
library holding. ([ADR 0013](decisions/0013-physical-collections.md))

**Context**: the file, copied into every archive, that maps record fields to
standard meanings. **Controlled vocabulary**: a list of allowed values kept as
data (a SKOS scheme). ([ADR 0012](decisions/0012-inherited-meaning.md))

**Preservation planning**: the work, done through policy, of keeping content in
formats that will stay readable.

**Usage observation**: a periodic count of how content was used (plays, views),
from an outside service.

## Naming discipline

When an established concept is meant, use its established word:

| Say | Not |
|---|---|
| admit | ingest, import, upload |
| Door | validator, gatekeeper, write service |
| World | knowledge base, facts store, metadata store, graph database |
| evidence, claim | facts, knowledge |
| Enrichment | pipeline, processing, analysis service |
| Policy | rules engine, config, workflow |
| plan / apply | sweep, orchestrator, scheduler, controller |
| assessment | decision, approval, ruling (for the record) |
| this repository | the archive |

The reverse also holds: do not file unrelated work under an existing word just
to avoid a new directory.

### Inheriting a term

Reuse a standard term before inventing one. Never accept a standard term's
looser meaning silently: either constrain it or use it only as a mapping. Always
write the prefix on the false friend: `arch:Entity` is not `prov:Entity`.
([ADR 0012](decisions/0012-inherited-meaning.md))

### Adding a noun

New nouns should be expensive. Before adding one, answer in a proposed decision
record:

1. What does it do that nothing else does?
2. Which verb does it own?
3. Which invariant does it enforce?
4. Why can it not be code inside an existing concept?
5. Does it own authoritative state?
6. Is it really a subsystem, or an operation, protocol, projection, library, or
   detail?

"Sweep" failed this test and became plan / apply.
