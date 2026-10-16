# 0052. Door verifies admissions incrementally from its own checkpoint

Status: accepted

Door re-verifies the whole archive on every admission. Before it plans a
batch, it reads and checks every committed record, every installed contract,
and the history of every immutable path. Before it acknowledges a commit, it
traverses every reachable object, runs `git fsck --full` on the whole object
store, and synchronizes every object file (ADR 0031, ADR 0034). The cost of
each admission therefore grows with the size of the archive, and the cost of
admitting an archive's history grows with its square.

The first semantic shadow measured this on 2026-10-15. At about a thousand
records and a few hundred commits, one batch (the migration's dry-run check, then an admission) took
about 75 seconds, and was getting slower with each batch. Most of that went into
starting about 4,000 `git` processes per snapshot. Reading each snapshot in
bulk and reusing the plan under the lock cut it to about 3 seconds. Those
changes leave what Door checks unchanged. The remaining cost is still
proportional to the archive: about 0.5 s of record checks per snapshot,
0.3 s of history and `fsck` traversal, and about 5,000 `fsync` calls on objects
that were already synchronized. The review application will admit small
batches often, so this cost would apply to every review.

## Decision

**Door keeps an operational verification checkpoint.** After Door has fully
verified a commit, it may record that fact in a checkpoint inside the
repository's Git directory, outside history. Door fully verifies a commit when
it checks it from scratch, or when it commits its own batch on top of a
checkpointed commit. The checkpoint names the commit and the Door build that
verified it (the running executable's file identity). It also holds the names
of the object store's pack files, the commit that added each immutable path,
the linear history, and for each record the object, shape, and superseded
revisions that planning needs. A checksum line covers the rest of the file.
Door writes the checkpoint only while holding the archive lock, and replaces it
atomically.

**A checkpoint lets Door skip work, never checks.** When HEAD is the
checkpointed commit and the checkpoint is intact, from the same Door build,
and its pack fingerprint matches, Door:

- plans from the checkpointed summaries, reading and parsing a record's bytes
  only when planning looks at that record (transition planning, which looks
  at every record, reads them in bulk);
- checks the proposal's records exactly as today;
- traverses and synchronizes only the objects reachable from the new commit
  that are not reachable from the checkpointed commit, inflating each new
  loose object and checking it against its name (a new object that is not
  loose gets the full baseline);
- writes a new checkpoint for the new commit after acknowledging it.

In every other case Door verifies the whole archive, as it does today, and
then writes a checkpoint. That includes a missing, torn, foreign or mismatched
checkpoint, any HEAD other than the checkpointed commit, and a changed pack
set. Coordinated Git maintenance (ADR 0034) runs under the archive lock and
removes the checkpoint.

**Checking old objects for decay becomes routine verification.** Today every
admission would notice a corrupted old object. With a checkpoint, `archive
doctor` (or an explicit full verification) notices it instead. Under ADR 0008
that check is routine, operational verification: success stays local, and only
a finding is recorded. A rewritten history cannot hide behind a checkpoint,
because a different history has a different HEAD commit.

## Why

- **The state model already allows it.** A checkpoint is the result of a
  routine verification: operational state, owned by Door, that can be lost at
  any moment. Losing it costs one full verification, not information.
- **Commit identity carries the history.** A Git commit names its whole tree
  and ancestry by hash. If HEAD is the commit Door verified, the archival
  content beneath it is the content Door verified. What a checkpoint stops
  re-checking is bit rot in object files, not meaning.
- **The trust boundary does not move.** The checkpoint lives in the Git
  directory, next to the refs and the archive lock. Anyone who can forge it can
  already rewrite refs. The checksum catches torn or corrupted writes. It is
  not a defence against someone who can write the repository.
- **ADR 0031's promise is unchanged.** Objects reachable from the checkpointed
  commit were synchronized before that commit was acknowledged, and only
  coordinated maintenance under the lock can replace them. The pack fingerprint
  catches a replacement that did not remove the checkpoint.

## Consequences

- One full verification runs after any HEAD change Door did not make, after
  maintenance, and after an upgrade of Door. Each later admission costs work
  proportional to its batch, plus reading the checkpoint and listing the tree.
  On the shadow's copy at about a thousand records, a batch went from about 75 seconds to
  about 0.6 seconds, and the first batch after a new build takes about
  3.4 seconds.
- The checkpoint format is internal to Door and versioned with the build. It is
  never evidence and never copied.
- `archive doctor` runs `git fsck --full --strict` over the object store and
  reports a failure as an issue. The runbook schedules it.
- Tests cover each fallback (missing, torn, foreign, stale HEAD, changed pack
  set) and include a test that makes a full re-read fail if it happens in the
  checkpointed case.

## Alternatives considered

- **Keep full verification on every admission.** It is simple, but it makes
  the review application's writes slower as the archive grows, and quadratic
  over a migration.
- **Cache only Git's checks and keep re-reading every record.** This removes
  the history and object-store work but leaves cost proportional to the
  archive in every admission.
- **Make other programs trust a projection.** This would make a regenerable
  store authoritative by accident, which the state model forbids.
