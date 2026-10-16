# 0020. The archive on-disk layout

Status: accepted

An archive must remain intelligible without this source repository, while
keeping evidence separate from storage and disposable projections. The
predecessor demonstrated that immutable JSON records beside git-annex content
are practical, but its source-shaped paths and checked-out views mixed identity,
location, and regenerable state.

## Decision

An archive root has this normative layout:

```text
archive.json
spec/<version>/VERSION
spec/<version>/archive-spec.md
spec/<version>/context.jsonld
contracts/sha256-<digest>.json
records/sha256-<digest>.json
content/sha256/<digest>
```

`archive.json` is the root manifest. At creation it contains only the layout
version, the archive identifier, and the governing specification identifier.
It is archive control state, not a record envelope. `archive init` creates it
and the copied specification tree together; changing its governing
specification remains unsupported until specification migration is defined.

Each `spec/<version>/` directory is an immutable, self-contained copy of one
specification version and its core JSON-LD context. Each file in `contracts/`
is the exact immutable contract bundle cited by a record; its filename is the
bundle's lowercase SHA-256 digest. Each file in `records/` is one immutable
record revision; its filename is the lowercase SHA-256 part of its
`revision_id`. A filename and the identity inside the file must agree.

`content/` is the git-annex worktree. A content path uses the lowercase SHA-256
digest of the bytes, not an object identifier, source name, or observed
filename. The path is a location, not an identity, and may be rebuilt from
records and git-annex state. Several objects that describe the same bytes use
the same content path.

Git history and the immutable files above carry evidence. Locks, admission
proposals, plans, indexes, views, caches, and other operational or regenerable
state are not part of the normative layout and must not be needed to recover
evidence. Programs build every normative path through one layout module.

This proposal does not define migration between specification versions or a
new digest algorithm. An implementation must continue to fail loudly for both.

## Why this is the smallest layout

The manifest supplies the archive UUID required by P-9 and ADR 0021. The copied
specification and contexts make the archive stand alone (ADR 0001, S-1,
S-13). Content-addressed contract and record files make citations checkable
without a registry. A single content tree gives git-annex a worktree without
making a mutable location part of an artifact (ADR 0003, INV-6). Everything
else is recoverable or belongs to another mechanism (INV-4).

The predecessor provides evidence that one-record-per-file Git history and a
git-annex worktree operate at the required scale. Its `originals`, `derived`,
and `views` directories are not copied: arrival paths are locations, while
derivatives deliberately kept are artifacts and views are regenerable.

## Alternatives rejected

- **Keep source, derived, and view trees as the archive contract.** Source
  names are observations rather than identity, a kept derivative is evidence,
  and a view is disposable (ADR 0003, INV-4).
- **Put object identifiers or source names in content paths.** Several objects
  may share content, and renaming or moving bytes must change no identity
  (S-9, INV-6).
- **Use mutable `current` or `latest` files.** Currentness is a projection and
  unchanged runs must not write history (S-18, INV-5).
- **Use a database or opaque object store for records.** It adds machinery
  without improving the readable, immutable Git mechanism already required by
  P-1 and ADR 0009.
- **Store proposals and locks under a normative directory.** They are
  operational state; losing them must not lose evidence.
