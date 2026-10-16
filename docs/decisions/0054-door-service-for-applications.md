# 0054. A long-running Door service for applications

Status: accepted

The rehearsal review application will write through Door often, in small
batches, while a person waits. Each `door admit` process starts from nothing:
it reads the checkpoint (ADR 0052), lists the committed tree, and rebuilds its
planning index before checking one small batch. That start-up work grows with
the archive. The checkpoint is also rewritten in full after every admission, so
its write cost grows too. At a few thousand records it is most of a megabyte.

## Decision

**Door can run as a service for one archive.** `door serve --socket <path>`
holds one archive's verified planning index in memory and accepts the same
requests as the command line: a dry run, an admission, or a transition, each
naming a proposal directory. Each answer is exactly the JSON the command would
print. Applications write through the service; the command line stays for
operators, scripts, and migration.

**The service is Door, not a second writer.** For every write it takes the
archive lock, checks that HEAD is the commit its index describes, and plans,
commits, and synchronizes exactly as `door admit` does (ADR 0002, ADR 0031).
If HEAD moved, because a command, maintenance, or anything else wrote, it
reloads from the checkpoint or verifies in full before answering. Requests are
handled one at a time. The service keeps nothing that a restart cannot
rebuild.

**The socket is local and private.** The service listens only on a Unix
socket created with owner-only permissions in a directory owned by the
operator. Network access, other users, and authentication beyond file
permissions are out of scope; exposing writes beyond the local operator needs
its own decision.

**The checkpoint is written in segments.** A checkpoint becomes a base file
and a chain of per-commit additions. Each addition names its parent commit,
lists only the paths and record summaries its batch added, and carries its own
checksum. Door appends one addition per commit, and compacts the chain into a
new base during maintenance or when the chain grows long. Every check in
ADR 0052 applies to the base and to each addition, and any broken link means
no checkpoint.

## Consequences

- An application write costs the work of its own batch, plus the fixed
  synchronization ADR 0031 requires.
- The review application needs no database of its own for archival state. It
  reads through World and writes through the Door service.
- Start-up after a restart costs one checkpoint read, or one full verification
  if the checkpoint is missing or stale.

## Alternatives considered

- **Keep one process per write.** Simple, but each write's start-up grows with
  the archive.
- **Let the application hold the index and call Door's library.** This would
  put a second writer in the application process.
- **An HTTP service.** Adds a network surface the local operator does not need.
