# 0008. Plan, then apply; history stays quiet

Status: accepted

Something has to bring the archive toward what policy wants, mostly without the
operator. It must not become an orchestration system, and it must not fill the
archive's history with noise.

## Decision

**Two steps, not a subsystem.** **Plan** evaluates policy against the archive
and lists what should change, each action with its reason (policy revision,
rules, facts). Planning changes nothing. **Apply** carries a plan out. Domain
knowledge lives in policy and in the tools that satisfy it (git-annex for
placement, enrichment tools for derivation), not in a generic loop.

**Plans are bound.** A plan records the policy revision and evidence it was
computed from. Apply re-checks every action first and refuses any whose
conditions no longer hold; a stale plan cannot act. This is what "intent Door
has sanctioned" (ADR 0002) means in practice: apply changes storage only to
carry out an action of a plan still valid against the authorized policy
revision, or a transition Door has already recorded.

**What runs unattended.** Copying, derivation, and publication under an
existing approval run without the operator. A drop runs unattended only when the
plan is fresh, the surviving copies were verified recently enough, and the
failure-domain requirement still holds without it; otherwise it waits for the
operator. A drop that reduces a content's failure domains is recorded, citing
the policy that caused it. Destruction runs only when a person recorded it.

**Placement through labels.** The planner decides, per object and in the policy
language, which failure domains should hold each content, and emits that answer
as an action: set the label (`want=hot`, `want=offsite`) where it differs from
what git-annex holds. Apply writes the changed labels before git-annex acts, so
a plan can be read before anything moves. Each git-annex group has one fixed
rule that reads the label. git-annex then moves the bytes, enforces copy counts,
and checks the other copies live before any drop. Because policy only adds,
content shared by several objects is wanted wherever any of them wants it.

**History records meaning, not activity.** Every record, commit, and git-annex
log entry should answer "what happened?" with something that happened. A rerun on
unchanged inputs writes nothing; an idle archive stays still. Time passing
counts only when it crosses something a rule names, such as a retention age or
the end of a usage period, and then writes exactly what that implies.

**Routine verification is operational.** A successful routine check (`git annex
fsck`) is kept locally on each host, outside history. Only a finding becomes a
record: missing content, a hash mismatch, a repair. If that local state is lost,
freshness is unknown and drops wait until copies are checked again.

## Rejected

- **A long-running reconciler or controller, or many unrelated timers.**
  Orchestration machinery for a problem two commands solve.
- **Copying raw facts into git-annex metadata** and writing rules as git-annex
  expressions, as the V2 spike did. It took about an hour per run on the real
  archive, rewrote every value in replicated history each time, and split policy
  across two languages.
- **Recording every successful check as evidence.** Thousands of meaningless
  records a week.
