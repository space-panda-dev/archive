# Invariants

These must always hold. Each has a stable number so code, tests, and decision
records can cite it (`INV-2`).

If an implementation cannot satisfy one, **stop**: write down the contradiction
and propose a decision record. Never work around an invariant. See
[AGENTS.md](../AGENTS.md#when-an-invariant-is-in-the-way).

## Design principle

**Desired state in policy; mechanism in code.** What the archive should hold
belongs in policy ("keep accepted WAVs in two failure domains", "transcribe
accepted rehearsal audio"). How it gets there belongs in code or configuration
("copy with git-annex", "split audio longer than 90 minutes", "use large-v3 on
this machine"). Before adding a special case to code, ask whether it is really a
desired state. Before adding a feature to the policy language, ask whether it is
really mechanism. The policy language stays less powerful than the code.

## Custody

**INV-1. No silent change.** An admitted object is never changed in place. Its
bytes change only through a new admission or a recorded transition.
([ADR 0003](decisions/0003-record-model.md))

**INV-2. One Door governs archival meaning.** Archival state changes only
through Door, the single authority, running on one host. Storage, operational
state, external systems, and projections are outside its jurisdiction; storage
changes only to carry out what Door has sanctioned. Door never needs bytes to
pass through it. ([ADR 0002](decisions/0002-one-door.md))

**INV-3. At most one active operator.** Handing an archive to someone else is a
recorded transition. Concurrent operators means separate archives.
([ADR 0001](decisions/0001-archive-preserves-evidence.md))

**INV-4. Regenerable state is disposable.** Caches, indexes, views, and
projections can be deleted without losing anything archival. If deleting one
would lose information, that information was never admitted, and that is a
defect. ([ADR 0001](decisions/0001-archive-preserves-evidence.md))

**INV-5. History records meaning.** Records, commits, and git-annex log entries
are written only when something meaningful happened. Rerunning anything on
unchanged inputs writes nothing. ([ADR 0008](decisions/0008-plan-and-apply.md))

## Records and claims

**INV-6. Location never changes a record.** Moving, copying, or dropping a copy
creates no revision and breaks no pin. ([ADR 0003](decisions/0003-record-model.md))

**INV-7. Judging never erases.** Conflicting claims coexist, each with its
provenance. An assessment is a further record, never an overwrite, and
"current" is computed, never stored.
([ADR 0005](decisions/0005-claims-review-and-authority.md))

**INV-8. Belief comes from provenance.** How strongly a claim is believed never
depends on whether a person or a machine made it, and there is no universal
confidence scale. Who may *cause* a consequence is a separate question.
([ADR 0005](decisions/0005-claims-review-and-authority.md))

**INV-9. Enrichment never changes its inputs.** It produces new output; output
worth keeping enters through Door. ([ADR 0006](decisions/0006-enrichment.md))

**INV-10. World never owns archive facts.** Anything World knows that the
archive relies on is first admitted as a claim, in its own admission, never
during policy evaluation. ([ADR 0011](decisions/0011-world.md))

**INV-11. Inherited terms never weaken a contract.** An imported term is either
constrained by an Archive profile or used only as a mapping.
([ADR 0012](decisions/0012-inherited-meaning.md))

**INV-24. An incomplete read says so.** Truncation, an unsupported contract, a
withheld record, an unresolved reference, and a conflict are results in their
own right. An empty answer means nothing was there, never that something could
not be read. ([ADR 0003](decisions/0003-record-model.md))

## Deletion and copies

**INV-12. Deletion is deliberate.** Retiring and destroying are recorded
transitions, always initiated by a person. Nothing is destroyed implicitly, and
unattended work carries out only destructions a person recorded. Every
consequential transition can explain itself.
([ADR 0004](decisions/0004-deletion.md))

**INV-13. Only real copies count.** Placement counts failure domains. Copies the
archive controls count; outside copies count only when recently verified;
projections and published copies never count.
([ADR 0009](decisions/0009-copies-and-custody.md))

**INV-14. Irreversible outside acts keep their reasons.** When the operator acts
outside Door on an archive verdict, such as erasing a card the archive said was
safe to erase, the verdict and the evidence behind it are recorded.
([ADR 0009](decisions/0009-copies-and-custody.md))

**INV-15. Byte rules govern digital content.** Destroy, copies, failure domains,
and placement apply to digital content. Physical items are records in the
collection. ([ADR 0013](decisions/0013-physical-collections.md))

## Policy and plans

**INV-16. The operator authorizes policy.** Anyone may propose policy; only the
operator authorizes it. Author and authorizer are recorded separately, and
machines never change authoritative policy. ([ADR 0007](decisions/0007-policy.md))

**INV-17. Policy only adds.** No rule cancels or lowers another. `not` appears
only in conditions over evidence, written so that a missing fact leads to the
more protective outcome. ([ADR 0007](decisions/0007-policy.md))

**INV-18. Unknown never reduces.** A fact that cannot be established never
causes a dropped copy, a retirement, or a withdrawal. A missing observation is
unknown, not zero. ([ADR 0007](decisions/0007-policy.md))

**INV-19. Plans are bound.** A plan records what it was computed from, and apply
refuses any action whose conditions no longer hold.
([ADR 0008](decisions/0008-plan-and-apply.md))

## Publication and disclosure

**INV-20. Publication needs a person's approval for that destination.** An
approval covers one destination (or a class the operator names) and exact
versions. Others' work needs a fresh approval before it leaves the operator's
circle; a change in rights only makes it eligible. An undeclared destination is
treated as distribution. ([ADR 0010](decisions/0010-publication.md))

**INV-21. Unknown rights never widen exposure.** Open and distribution
destinations require a rights claim that permits them.
([ADR 0010](decisions/0010-publication.md))

**INV-25. Disclosure is granted, never assumed, and always recorded.** Content
leaves the operator's custody only to a destination that policy grants for it,
and each disclosure is recorded with the policy revision that allowed it.
([ADR 0016](decisions/0016-disclosure.md))

**INV-26. A limit binds until its author lifts it.** The strictest sensitivity
claim on an object caps its disclosure, and a limit set by a person the content
records is lifted only by that person. The operator may go past it only for one
named destination, no further out than service, with a recorded reason.
([ADR 0018](decisions/0018-people-in-the-archive.md))

## This repository

**INV-22. Spool knows nothing of Archive.** Archive may use Spool; Spool never
depends on Archive. ([ADR 0014](decisions/0014-boundaries-and-languages.md))

**INV-23. No personal archival data here.** Fixtures are synthetic.
([ADR 0001](decisions/0001-archive-preserves-evidence.md))
