# 0002. One Door governs archival meaning

Status: accepted

Something has to decide what the archive officially says. If several programs
could each change archival state, a change could be valid to one and invalid to
another, and nobody could say what the archive actually holds.

## Decision

**Door is the only authority for archival meaning.** Nothing becomes "the
archive says X" until Door admits it or records a transition. Door validates
structure, meaning where the invariants require it, whether a transition is
legal, policy constraints, governance authorization, and provenance, then
commits or refuses. It decides whether a change is *valid*, never whether it is
*wanted*; policy says what is wanted.

**Door's jurisdiction is archival state, not every durable byte.**

| State | Authority |
|---|---|
| Archival state: evidence, and transitions that change its meaning (admission, claims, assessments, policy, publication, supersession, retire, destroy, reductions in failure domains, operator succession) | Door |
| Storage: bytes copied, moved, dropped; git-annex location logs and placement labels | git-annex and the filesystem, acting only under intent Door has sanctioned |
| Operational state: verification bookkeeping, plans, progress, leases | The mechanism that owns it; disposable |
| External state: photo services, Spool, a seedbox's download client | The external system; the archive may observe it |
| Projections: World, indexes, views, "current" | Rebuilt freely; never authoritative |

**Door governs authority, not bytes.** Archives hold hundreds of gigabytes.
Bytes are staged, copied, and placed by git-annex and the filesystem; they
become archival when Door sanctions them. Door never needs a large file to
stream through it.

**One Door, on one host.** Door's implementation may span modules, but
validation and authority are never split into separately authoritative
services. It runs on one machine. Other machines (a seedbox receiving
downloads, a laptop importing a card) are staging hosts: they add bytes to their
own git-annex repository, computing the hash where the bytes live, and propose
an admission. Door verifies through git-annex that the content is there, then
admits the record.

Door keeps no unique durable state of its own; its results live in the archive.

## Rejected

- **Door as the writer of every durable byte.** It would have to mediate
  git-annex's own bookkeeping and become an all-purpose gatekeeper.
- **A Door on each host.** Two hosts superseding the same object at once would
  split its history; that would require distributed consensus for no benefit.
- **Validation and authority as separate services.** See the opening paragraph.
