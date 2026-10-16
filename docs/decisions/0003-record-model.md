# 0003. The record model

Status: accepted

Everything the archive knows has to be stored in a form that stays readable,
checkable, and unambiguous for decades, and that can grow new kinds of
knowledge without redesigning the core.

## Decision

**Four shapes.** Evidence is stored as immutable record revisions, each in one
of four shapes:

- **artifact**: an object whose bytes the archive keeps (an original, or a
  derivative it chose to keep);
- **entity**: a durable subject the operator asserts (a song, a person, a book
  on the shelf);
- **claim**: a typed, versioned statement about other objects;
- **assessment**: a judgment of an exact claim revision, or a settlement
  between competing claims.

**Every transition is a claim of a transition kind.** Retire, destroy,
publication approval and withdrawal, failure-domain reductions, succession,
reclaim verdicts, and disclosures are claims whose kind names the transition
and whose references name what it changes. Their contracts ship with the
specification, and Door implements their effects. An assessment judges whether
a claim is true; approving a consequence is governance, not judgment (ADR 0005),
so it is never an assessment. Retire is its own claim, not a bare supersession:
taking an object out of the collection is not revising it (ADR 0004).

**A policy revision is text plus an adoption.** The rules text is an artifact.
A claim of kind `policy_revision` adopts it, naming its author and the operator
as authorizer, and supersedes the previous adoption. Plans cite that claim's
revision.

**Revisions, not edits.** A change is a new revision; an old one is never
edited. Supersession is explicit and never inferred from timestamps, names,
hashes, or locations. A reference can pin an exact revision.

**Three identities.** An **object** has a stable, opaque identifier that is not
a content hash. **Content** is bytes, identified by hash; several objects may
share it. **Location** is where bytes currently sit. Location is not part of an
artifact's record, so moving, copying, or dropping a copy never creates a
revision and never breaks a pin.

**Provenance in the envelope.** Every record names the program that wrote it
(`by`) and the agent that asserted it (`agent`: a person, a model, or a
process). Payloads hold content only; who and how live in the envelope and its
references (ADR 0012).

**Structure is types; kinds are data.** The four shapes, the envelope,
identity, references, supersession, retire and destroy, and policy revisions
are Haskell types in Door. Each claim kind (for example `audio_transcript`
version 2) is a **contract**: a JSON Schema document with a small context
mapping its fields to standard terms, identified by version and digest, stamped
into every archive, and cited by the records that use it. Door validates
payloads against contracts generically, so a new kind of enrichment result
needs a new contract, not a new Door. A contract is where its kind's "current"
rule belongs, written in the policy language. Until the policy grammar and a
kind's rule are defined, its contract has none, and asking for that kind's
current claim fails as unsupported. Everything that reads records
(Door, the planner, the reading tools, World) reads the contract itself; none
keeps its own copy of a contract's fields or rule.

**"Current" is a projection.** Which claim is current is computed from claims
and assessments by each kind's rule. It is never stored as evidence, and when
claims compete it reports a conflict instead of choosing.

**Time is exact.** Media boundaries are stored as integer sample offsets with
their sample rate, never as floating-point seconds. Intervals are half-open, as
in Media Fragments; seconds are derived for display. An exact point uses a
small point selector, because a Media Fragments `#t=30` means "from 30 seconds
on".

## Rejected

- **More shapes** (publication, policy, transition, derivation as their own
  shapes). Each fits as a kind within the four.
- **Location inside the artifact record.** In the V2 spike this made every
  placement change mint a revision and stale every pin.
- **Claim kinds as Haskell types.** Every new enrichment output would need a
  new Door build.
- **A stored "current" field.** It would drift from the evidence and silently
  choose between competing claims.
