# 0067. Matches and alignments pin a derivation run

Status: accepted

`work_match` version 1 requires a `run` reference whose target rule is only
"a Claim": any claim at all satisfies it, which is a structural gap the
specification papers over by forbidding admission (D-14). `capture_alignment`
version 1 has the same untyped optional `run`. Version 1 also compares only
song occurrences, but a review client needs a suggestion while it names a
kept selection, before any occurrence exists.

## Decision

**`archive.claim/work_match` version 2** is version 1 with two changes:

- its `run` reference MUST pin an `archive.claim/derivation_run` version 1
  Claim revision (ADR 0064);
- the compared audio is exactly one of `occurrence` (a song occurrence
  version 2 Claim revision) or `selection` (an audio selection version 2
  Claim revision). `corpus`, `work`, `parameters`, the payload, and the
  distinctness rule are unchanged.

**`archive.claim/capture_alignment` version 2** is version 1 whose optional
`run` MUST pin an `archive.claim/derivation_run` version 1 Claim revision.

**Version 1 of both stays installed** with its exact bytes. Nothing admits
a version 1 match: no proposer can satisfy its run, and the specification's
prohibition on reading one as identity or current stays.

## Consequences

- A matcher in `enrich/` can admit matches with their run, corpus, and
  parameters, and World can show them as suggestions.
- A match still never establishes a Work; a person's `performance_work`
  does (ADR 0040).

## Alternatives considered

- **Tighten version 1 in place.** A released bundle never changes
  (ADR 0057).
- **A separate contract for selection matches.** The meaning is the same
  match; only the compared record differs.
