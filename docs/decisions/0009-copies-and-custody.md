# 0009. Copies, custody, and storage

Status: accepted

Bytes live in many places: laptops, drives, a seedbox, cloud storage, photo
services, backups, media-server libraries. The archive has to know which of
those actually protect its content, and where its records and bytes are kept.

## Decision

**Git holds records; git-annex holds content.** Records are small immutable
files committed to Git. git-annex tracks where content is, enforces copy counts,
and refuses unsafe drops. Git history is not rewritten in normal operation.

**Every location has one relation to archival content.**

- **Managed**: git-annex tracks it, can verify it on demand, and can drop from
  it; nothing else writes to it. A laptop, a cold drive, a full git-annex
  repository on the seedbox, an encrypted git-annex remote in cloud object storage.
- **Observed**: the archive can check the bytes by hash but does not control
  them. A hosted or self-hosted photo library, a copy on a friend's drive. The archive
  never drops an observed copy; destroy reports it.
- **Projection**: the same bytes seen again. A symlink into a media server's
  library, a browsable mirror.
- **Source**: where content came from before admission. A recorder card, a
  download folder, a photo service.

**Placement counts failure domains.** Two repositories on one disk, or two
folders in one provider account, are one domain. Which domain a location belongs
to is configuration. Managed copies count; observed copies count only when
recently verified; projections and published copies never count.

**Offsite content is managed.** Content goes offsite in managed domains (for
example an encrypted git-annex remote in cloud object storage), so destroy can reach it. Snapshot
backups such as restic hold only records and Git history: small, precious, and
never destroyed.

**Sources are cleared on a verdict.** The archive can say that a source is safe
to clear ("card X is safe to erase") when survivors satisfy policy. Clearing it
is the operator's physical act, outside Door, and it cannot be undone, so a
verdict the operator acts on is recorded as evidence with the survivors, their
domains, and the verification state that justified it. "Why did the archive
think this card was safe to erase?" must be answerable years later. Photos in an
outside service become sources the same way once admitted.

**The seedbox is a full copy.** A seedbox holding a full git-annex repository
(installable without root) is its own offsite domain. Acquisition there is: move
the finished download into the repository, add it, symlink it back where the
download client expects it so seeding continues, and propose the admission to
the Door host.

## Rejected

- **Counting repositories instead of failure domains.** Two copies in one house
  do not survive the house.
- **Counting mirrors and symlinks as copies.** They are the same bytes.
- **Content in snapshot backups.** Destroy could not reach it, and a restore
  would bring back deleted junk.
