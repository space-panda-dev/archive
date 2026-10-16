# 0034. Coordinate Git object-store maintenance with Door

Status: accepted

[ADR 0031](0031-power-loss-durability.md) requires Door to establish a durable
committed history before it acknowledges a persistent mutation. Door can
synchronize the Git objects it just wrote, but the new commit also depends on
older objects and packs. Git maintenance can replace packs and remove loose
objects. The archive lock currently protects Door's own mutations, not a
separate `git gc`, repack, or prune process. A scan and synchronization of the
existing object store cannot prove a stable baseline while such a process runs
outside that lock.

## Decision

Git object-store maintenance for an Archive MUST coordinate with the same
archive lock used by Door. Door MUST refuse a durable-success claim when it
cannot establish a complete, self-contained local object store and a stable
reachable-object baseline. The initial supported repository has no alternate
object store, shallow boundary, or promisor object dependency. Door must check
these conditions, synchronize the reachable objects and their storage before
acknowledging a new commit, and fail closed when the proof is incomplete.

An operator may manage private copies and configure trusted absolute Git and
git-annex executable paths. Those acts do not make another program an
authority for archival meaning: [ADR 0002](0002-one-door.md) still reserves
archival commits for Door. Git maintenance performed by the operator remains
possible when it participates in the archive lock. Direct, uncoordinated
maintenance while Door is mutating is outside the supported workflow.
The lock mechanism and operator command surface belong in implementation,
not in the archival record format.

This decision adds no weaker exception to ADR 0031. A successful Door response
still promises a recoverable complete commit after power loss on a filesystem
that honors the required synchronization requests. A failure to acquire the
coordination lock, prove the object-store topology, or synchronize the baseline
must prevent success; a failure after ref advancement must be reported as
uncertain.

## Why

Git's own maintenance lock does not cover every object-store writer. Git
documents that `git gc` does not take the same lock as `git maintenance run`.
Git also supports borrowed object stores through alternates. Door cannot
prove durability of an external store or prevent uncoordinated maintenance
from replacing a pack between a baseline scan and commit acknowledgement.
See the Git [maintenance](https://git-scm.com/docs/git-maintenance) and
[repository layout](https://git-scm.com/docs/gitrepository-layout)
documentation.

## Alternatives left open

- Support concurrent direct Git maintenance through a stronger shared
  snapshot or locking mechanism. No such mechanism has been demonstrated for
  the accepted power-loss promise.
- Support alternate, shallow, or promisor object stores after their own
  durability and completeness rules are specified and tested.

Draft wording for a later specification release is in the
[specification amendment](../proposals/0034-git-maintenance-spec.md). The
current specification is unchanged.
