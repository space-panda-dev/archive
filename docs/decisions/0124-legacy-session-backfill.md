# 0124. Backfill missing sessions from the legacy grouping and file order

Numbering note: accepted as ADR 0121 on 2026-10-25; mechanically renumbered
to ADR 0124 when integrating main, preserving its published ADR 0120.
The accepted decision and operator approval are unchanged.

Status: accepted (operator, 2026-10-25). The operator approved reconstruction
from the legacy grouping and file order, with source provenance and explicit
review of ambiguity. This decision does not authorize an archive mutation or
replace the per-run scratch and operator procedure.

## Context

A recording already present as an Artifact may have no `recording_session`
Entity or `session_parts`. ADR 0070's amendment lets `capture import` adopt known content that belongs
to no session, using its recorder grammar grouping. That is not a migration
of the legacy session grouping. ADR 0107's parts-only path joins an existing
session; it does not recover the legacy grouping either. Renaming an Entity cannot supply missing
membership or ordering. The review client needs the same naming and metadata
reader for legacy and native sessions, without a regrouping interface.

## Decision

Prepare a read-only inventory from the legacy source snapshot: its explicit
session grouping, ordered files per source, and each file's content digest.
Resolve each digest to the exact existing Artifact revision. Preserve the
legacy group and file order rather than applying a recorder grammar to
reconstruct it. Existing session Entities and parts are inventoried first;
never create a duplicate just because a name differs.

For an unambiguous absent session, propose a `recording_session` Entity and
one ordered `session_parts` Claim per source through Door, without adding or
copying content. The primary source and its first part establish ADR 0048's
`archive.audio/session` identity `session-<first-part SHA-256>`. Preserve the
legacy label as a name, never as date evidence. The operator reviews missing
files, digest mismatches, conflicting groupings, duplicate membership and an
ambiguous primary source before any proposal is admitted. Such groups remain
unsupported until resolved; no sorting heuristic silently decides them.

Every translated grouping carries durable provenance to the admitted source
snapshot and its exact source record locator, alongside the produced Entity
and parts Claim revisions. The mechanism must publish a contract carrying
that mapping before it writes; the existing `archive.migration/translation`
version 1 cannot be repurposed, because it restricts rule values and its
`translated` targets are Claims, not Entities. A new additive bundle must
preserve those old meanings and name this translation explicitly. Operational
reports alone are insufficient provenance.

Capture-start claims are separate evidence under ADR 0107. A date already
asserted by a legacy source can be translated with its source provenance;
parsing a display label or substituting Door's admission time is forbidden.
A missing date remains unknown. Session ensemble associations follow
[ADR 0123](0123-session-ensemble-association.md); no band is inferred from
membership or from which relay served the proposal.

The backfill is an explicit operator command and reviewed proposal batch,
not an automatic side effect of opening, naming or saving a review, and not
an extension of repeat-card import. Stable identities and expected heads
make an unchanged repeat write nothing. Revisions never erase conflicting
legacy evidence (INV-7).

## Proof before use

Synthetic tests cover grouping and rollover order preserved verbatim,
missing files, duplicate content, ambiguous primary source, an already named
native session, a conflicting existing grouping, provenance target types,
and identical retry. Rehearse admitted proposals and a second no-op run on a
scratch archive. Compare before/after heads and exact produced revisions;
record unresolved groups. The operator applies the reviewed procedure to
any archival environment. No regrouping or reordering UI is introduced.
