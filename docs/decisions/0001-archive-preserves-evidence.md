# 0001. Archive preserves evidence, is curated, and stands alone

Status: accepted

Archive has to be useful for decades, across changes of software, hardware, and
possibly owner. That needs a clear statement of what it is for and what it is
not.

## Decision

**Archive preserves evidence.** Evidence is anything whose loss would lose
information: original files, the subjects and claims the archive records,
judgments of those claims, machine results deliberately kept, the policy the
archive runs under, and the history of consequential changes. Archive owns
this evidence; it is not blob storage with the real knowledge kept elsewhere.

**Regenerable state is not evidence.** Caches, indexes, views, query stores, and
the answer to "which claim is current?" can be rebuilt. Losing them costs time,
not history. Generating something does not make it evidence; admitting it does.

**Archive is curated, not append-only.** Over decades, some things deserve to
go: junk, duplicates, mistakes, material that should not be kept. The rule that
holds instead is: an admitted object is never silently changed, and archival
state changes only through Door (ADR 0002). Deletion is ordinary, deliberate
work (ADR 0004).

**Archive stands alone.** It works with sibling projects (Spool, the review app,
a media server) only through published protocols, files, and programs. There is
no shared base library or umbrella framework, and none is built to unify
projects.

**One operator at a time.** The operator holds governance authority over an
archive (ADR 0005). An archive has at most one active operator; handing it to
someone else, or recovering it after its operator is gone, is a recorded
transition. Several concurrent operators means several archives, which share
only through publication and Spool.

**Three things share the name.** Archive the system, an archive on disk, and
this source repository are different things. Personal archival data never lives
in this repository.

## Rejected

- **Append-only.** It would be violated in practice (a terabyte of junk, a
  duplicate import) and then abandoned wholesale. A rule that can actually hold
  is stronger than one that cannot.
- **A shared framework across sibling projects.** It would couple projects that
  should be able to change or disappear independently.
