# 0023. Admission uses metadata proposals and git-annex staging

Status: accepted

Door must validate very large content without becoming its transport, while no
staging host may write archival meaning. A failed batch must not leave a
successful prefix, and a retry must not create history. The predecessor's
metadata batches, expected heads, exact path staging, and git-annex keys provide
evidence for those mechanics; its artifact envelope and commands do not define
this protocol.

## Decision

A staging host adds bytes to its own git-annex repository and computes their
SHA-256 digest, size, and annex key there. It makes that repository available
to the Door host as a configured git-annex remote. Configuration and transport
are not archival evidence.

The staging host proposes an admission by placing a metadata-only proposal
directory on the Door host and invoking `door admit --proposal <directory>`.
Door does not accept an admission over standard input or receive content bytes
inside the proposal. The directory contains:

```text
manifest.json
records/<proposal-name>.json
```

`manifest.json` is a closed object with these fields, all required even when
their arrays are empty:

```json
{
  "protocol": "archive-admission/1",
  "records": ["records/0001.json"],
  "identities": [{
    "object_id": "urn:uuid:...",
    "namespace": "archive.core/artifact",
    "name": "capture_7f3a"
  }],
  "contents": [{
    "digest": "sha256:...",
    "size": 123,
    "annex_key": "SHA256E-s123--...",
    "staging_remote": "import-laptop"
  }],
  "expected_heads": [{
    "object_id": "urn:uuid:...",
    "revision_id": null
  }]
}
```

Proposal paths are normalized relative paths beneath the proposal directory.
They must name distinct regular files, never symlinks, and may contain no
empty, `.` or `..` component. `records` determines order; directory enumeration
does not. A proposal record is the envelope proposed by ADR 0022 without
`revision_id` or `at`.

Each `identities` row supplies the allocation input defined by ADR 0026. There
is exactly one row for every new object in `records` and no row for an existing
object.

Each `contents` row names one exact byte sequence and a configured staging
remote. Every content item referenced by a proposed artifact appears exactly
once, and every declared item is referenced. `expected_heads` contains exactly
one row for every object the batch creates or revises: `null` means that the
object must not exist, and a revision identifier pins the one head that must
exist. Missing, duplicate, extra, or stale rows are refused.

Door performs admission under one archive lock:

1. Read and retain the exact manifest and draft bytes; reject unknown fields,
   duplicate JSON keys, unsafe paths, and changed files.
2. Validate the whole batch's envelopes, contracts, references,
   supersessions, expected heads, and content declarations without writing.
3. Ask git-annex to obtain each declared key from its configured staging
   remote. Door never streams the object through its own input, output, or
   memory.
4. Require the key to be locally present, run local fixity verification, and
   independently compare both SHA-256 and byte size with the proposal. A
   remote location log or key name alone is not verification.
5. Compute every revision, recheck the archive heads and retained proposal
   bytes, create the canonical content paths and record files, stage only
   those exact paths, and make one Git commit.

The commit is the point at which the records become archival. Record files are
written through temporary files and atomic renames. A failed proposal leaves
no authoritative record and no partial commit. Content transferred before a
failure remains explicitly reported, unadmitted storage for later retry or
cleanup; Door never silently deletes it or mistakes presence for admission.

Before testing the proposal's original expected heads, Door checks whether its
entire computed revision set already exists as one committed batch. If so, the
identical retry returns those stored revisions and writes no record, commit, or
location entry. If only part of the set exists, or no complete retry exists and
an expected head changed, Door refuses rather than folding a different
operation into the retry. `door admit --dry-run` validates metadata and
predicts revision identifiers without transferring content or claiming that
fixity was verified.

This protocol does not choose placement, retention, or desired copy counts.
Those remain policy. It also does not define specification migration or a
network service for Door. A staging host may transport the proposal by any
operator-chosen mechanism; only the local directory above is Door's interface.

## Responsibility boundary

Git-annex owns byte transfer, key storage, local presence, fixity operations,
and location logs. Door owns parsing and validating the proposal, checking the
content result, enforcing archive invariants and expected heads, computing
record identities, and committing archival meaning. A successful git-annex
transfer is never by itself an admission (ADR 0002).

The exact git-annex operations used by implementation remain subject to the
INV-5 experiment required by ADR 0015. If an unchanged verification or link
operation writes history, implementation stops at that gate; this proposal
does not reinterpret the write as meaningful.

## Alternatives rejected

- **Send bytes through Door.** It makes the authority service a large-object
  transport, contrary to INV-2.
- **Trust the proposed digest, annex key, or remote location log.** Each can
  describe bytes that are missing or different; local fixity and size must be
  checked.
- **Let a staging host write records.** It creates a second Door and violates
  S-2.
- **Commit records one at a time.** A later refusal would leave a severed,
  apparently successful prefix.
- **Discover records by walking the proposal directory.** Enumeration order
  and stray files would change an admission without changing its manifest.
- **Treat transferred bytes as admitted evidence.** Presence is storage state;
  only Door's committed records change archival meaning.
- **Persist the proposal as evidence.** Proposals are disposable operational
  state; admitted records preserve every fact the archive relies on.
