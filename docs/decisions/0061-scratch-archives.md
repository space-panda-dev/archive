# 0061. Scratch archives for testing real commits

Status: accepted

A client that writes through the relay needs to try real commits against
realistic data. The shadow cannot take them: a commit would stop it being a
faithful migration of V2 (ADR 0056), and the real archive is evidence. Until
now the only writable archives were ones a test built from nothing.

The operator decided on 2026-10-15 to allow scratch copies, and gave a
standing approval for two acts on the archive machine: taking a read-only
snapshot of a source archive (today the shadow, later the real archive), and
creating, resetting, or deleting scratch copies. Any other command against the
shadow or the real archive still needs approval each time.

## Decision

**A scratch archive is one writable snapshot of the source.**
`shadow/scratch.py create` takes a Btrfs snapshot of the source archive's
subvolume, and that snapshot is the scratch archive. Taking it only reads the
source, as a read-only snapshot would; only the copy is writable. The snapshot
is atomic, so the copy is one consistent state of the source: at worst the
state of a power loss, which Door recovers (ADR 0031). A source that is not its
own Btrfs subvolume is refused, because snapshotting a larger subvolume would
copy unrelated data; making it a subvolume changes the source and needs
approval.

**None of it needs root.** An unprivileged user may snapshot a subvolume they
can read, but without the `user_subvol_rm_allowed` mount option may not run
`btrfs subvolume delete`, and older `btrfs subvolume show` needs privileges
too. So when `show` is refused for any reason but "not a subvolume", the tool
asks the file system instead: the root of every Btrfs subvolume is inode 256.
When `delete` is refused, it empties the copy and removes the directory, which
Linux 4.18 and later allow the owner of an empty subvolume. A read-only
snapshot cannot be emptied, which is why the tool no longer takes one between
the source and the copy: an unprivileged user could never remove it.

**Each scratch archive has its own services.** The tool builds a new World
store from the copy, and starts the copy's own `door serve` and a relay with
role `scratch` and commits on, on a port the operator names (ADR 0060). It
publishes that port with `tailscale serve` only when asked. The services are
transient systemd user units; a reboot stops them, and `reset` starts them
again. The relay takes its callers and origins from the source's relay
configuration, so the same people can use it.

**Reset and delete are total.** `reset` stops the services, deletes the copy
and the World store, and takes a fresh snapshot of the source's current
state. `delete` also removes the scratch directory and the published
port. Everything lives under one directory per scratch archive, by default
`~/archive-scratch/<name>`, with the scratch state in `scratch.json`.

**Scratch is never evidence.** A scratch archive shares the source's archive
identifier, so its records could be mistaken for the source's. It is
operational state (see the state model), deleted at will, and its records are
never copied back. Its relay's role says so in every answer.

## Consequences

- A client's commits can be tested against the shadow's real records without
  touching the shadow.
- Btrfs snapshots share unchanged blocks, so a scratch archive costs the space
  of what it changes.
- The operator's standing approval names "a read-only snapshot of the
  source". The one writable snapshot reads the source exactly as that would;
  the difference is only that the copy is writable from the start.
- The first creation from the shadow depends on the shadow's archive being its
  own subvolume. If it is not, making it one is a separate approved operation.

## Alternatives considered

- **A reflink copy instead of a snapshot.** Also cheap on Btrfs, but not
  atomic: a copy made during a write could mix two states.
- **Commits on the shadow, undone afterwards.** Undoing a commit rewrites
  history Door has published, and the shadow would stop being faithful in
  between.
- **A fresh fixture archive instead of a copy.** Useful in CI (ADR 0062), but
  it holds none of the real records whose shape a client must handle.
