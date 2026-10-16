# 0053. Layout archive/2 fans out record and content paths

Status: accepted

ADR 0020 puts every record in one `records/` directory and every content
location in one `content/sha256/` directory. Git stores a directory as a tree
object that lists all of its entries, and a commit that adds one file rewrites
the whole tree of every directory on the path to it. With one flat directory,
each admission therefore writes a tree proportional to the whole archive, and
an archive's history grows with the square of its size.

The first semantic shadow measured this on 2026-10-15. At a few thousand
records the `records/` tree was a few hundred kilobytes, rewritten by every
commit, and most of the shadow's Git objects after a few hundred commits were
old copies of it. At 100,000 records each
admission would write about 10 MB of tree. Archives are expected to grow for
years, through many small review batches.

## Decision

**A new layout, `archive/2`, places each record and content location two
directory levels below its root, named by the first four hexadecimal
characters of its digest:**

```text
records/<h0h1>/<h2h3>/sha256-<digest>.json
content/sha256/<h0h1>/<h2h3>/<digest>
```

`<h0h1>` and `<h2h3>` are the first and second pairs of characters of the
lowercase SHA-256 digest. Everything else in ADR 0020 is unchanged: the
filename still equals the digest, the path is still built only through the
layout module, and `archive.json`, `spec/`, and `contracts/` keep their
places. Contracts stay flat because an archive cites a small, slowly growing
set of them.

**Specification 0.0.6 defines layout `archive/2`.** `archive init` writes
`"layout": "archive/2"` and governs new archives with specification 0.0.6.
Specification 0.0.6 otherwise carries forward 0.0.5's rules and
specification-owned contracts unchanged.

**This build supports only `archive/2`.** Door and the read commands refuse
an archive that declares `archive/1`, with a message naming this record. The
only `archive/1` archives were disposable: the first semantic shadow and test
archives. Keeping both layouts would leave one of them untested in Door's
crash-safe installation code. An `archive/1` archive is read with an earlier
build or rebuilt. Moving an archive between layouts is specification
migration, which remains unsupported (ADR 0020).

## Why two levels

One level of 256 directories helps until each directory holds thousands of
entries, then has the same problem. Two levels give 65,536 directories. A
commit rewrites the root `records/` tree (at most 256 entries), one tree of at
most 256 entries per first-level directory it touches, and one small leaf per
record. At a million records a ten-record batch writes about 100 KB of trees,
compared with about 100 MB for one flat directory. Git's own object store uses
the same hexadecimal fan-out.

## Consequences

- Each admission's Git writes no longer grow with the archive.
- The first semantic shadow is rebuilt with `archive/2`. The `archive/1` copy
  is kept, readable with the build that wrote it, only until the rebuilt
  shadow is compared with it.
- Door removes a fan-out directory it created when a refused or recovered
  batch leaves it empty, so a refusal leaves `records/` as it found it.
- Programs that read archives through `archive export` or World are
  unaffected. Programs that construct archive paths themselves must use the
  layout module or its Python counterpart, and are tested against both
  layouts.

## Alternatives considered

- **One level of fan-out.** Simpler, but at a million records each touched
  directory still holds about 4,000 entries.
- **Keep the flat layout and repack history.** Packing deltas old trees well,
  but every admission still writes and hashes a full-size tree, and loose
  objects accumulate until maintenance.
- **Migrate `archive/1` archives automatically.** This would define
  specification migration in passing, which ADR 0020 keeps open.
