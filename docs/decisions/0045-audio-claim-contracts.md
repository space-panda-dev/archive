# 0045. Audio claims bind exact selections to occurrences

Status: accepted

ADR 0044 settles the identities and meanings of audio selections and song
occurrences. It does not settle their closed, portable representation. That
choice is durable: an archive keeps every contract it cites, and a weakly
validated link could cause World to mistake an arbitrary Claim for a musical
rendition. Specification 0.0.3's `performance_work` version 1 checks only
that its `performance` target has Claim shape (ADR 0042).

## Decision

**Install three new claim contracts in the next specification release.**
`archive.claim/audio_selection` version 1 contains a nonempty ordered
`pieces` payload. Each piece has `start_sample`, `end_sample`, and
`sample_rate` as integers. Its `refs` array contains one exact Artifact pin
per piece, in the same order, each with role `piece`. Door checks equal
lengths, nonnegative starts, ends strictly after starts, positive sample
rates, and an exact committed Artifact revision for every reference. It does
not claim that a sample offset is within the content's duration unless a
separately admitted measurement supports that check. Pieces may repeat an
Artifact or cross Artifact boundaries; adjacency is not required.

`archive.claim/song_occurrence` version 1 has an empty payload and one or
more exact `selection` references to Claims citing
`archive.claim/audio_selection` version 1. Its membership is a set: Door
rejects duplicate pins and requires a canonical order by object and revision
ID so a reordered proposal does not write a meaningless revision. The
occurrence asserts one rendition; its references may represent concurrent
capture perspectives. It does not contain a title, Work identity, or review
status.

`archive.claim/performance_work` version 2 retains version 1's empty payload
and exact `performance` and `work` references, but Door additionally requires
the `performance` target to cite the song-occurrence version 1 contract.
Version 1 is retained unchanged for archives that cite it. World may show a
version 1 link as a structural assertion, but only version 2 establishes the
typed song-occurrence-to-Work traversal.

**Keep the checks in a small contract profile.** Extend the installed
contract profile's `x-archive-reference-targets` rule to describe Artifact
shape, repeated reference roles, and a Claim target's required contract
citation. Add one explicit profile rule for index alignment between the
selection's payload pieces and its `piece` references, and one for canonical,
unique unordered membership. Door implements these checks generically from
the installed bundle rather than recognizing these claim-kind names in a
special admission branch. The schema and profile rules are copied into every
archive with the specification release; unsupported profile rules fail
closed.

**Project the chain without currentness.** World retains each exact selection
and occurrence Claim revision and projects each selection piece with its
position, source pin, bounds, and sample rate. It relates an occurrence to its
member selections and a version 2 performance-to-Work Claim to the occurrence
it pins. These relations remain scoped to their asserting Claims and preserve
agents and assessments. World does not materialize one winning occurrence,
title, or Work until the relevant current rules are accepted.

The review PWA's gestures, draft state, title annotations, assessments,
retractions, and migration decisions are outside this contract decision.
They must not be inferred from this storage shape.

## Alternatives considered

- **Put source IDs inside each payload piece only.** Core reference checks
  and generic readers would not see those pins as references.
- **Create a Claim for each span.** This adds an identity and admission for
  every piece even when only the assembled selection has durable meaning.
- **Assume payload and reference arrays align without Door checking.** An
  extra, missing, or reordered reference could silently change the selected
  audio.
- **Rewrite `performance_work` version 1.** An archive may cite its existing
  bytes; tightening them in place would make historical interpretation drift.
