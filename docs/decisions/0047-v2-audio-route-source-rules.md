# 0047. Source translation rules for the V2 audio route

Status: accepted

ADR 0040 accepted the audio route's four contracts but left five
source-boundary gaps, listed in the
[release readiness note](../proposals/v2-migration-release-readiness.md#audio-route-blockers).
The route cannot be released, and the first V2 semantic shadow cannot claim
it, until each gap has an answer (ADR 0046). The operator chose these answers
on 2026-10-14. They govern translation of the frozen V2 bundle only; they do
not change native Archive selection or occurrence meaning (ADR 0044).

## Decision

**1. A linear correction chain is one selection.** V2 mints a fresh random
`segment_id` for every record, and a correction names exactly one predecessor
in `supersedes_segment_id`. An unbroken chain of single-predecessor
corrections on one source artifact is one Archive audio selection. Each
source record becomes one revision of that selection, in chain order. The
selection's identity name is `v2-segment-<lowercase-hex-of-source-id-utf8>`
of the chain's root record, which is the case ADR 0040 reserved for a source
ID that names the enduring selection. A fork (two successors of one record),
a missing predecessor, a cycle, or a successor on a different artifact leaves
every record of that chain unresolved.

**2. A performance ID is one assertion object; a membership change is
another.** `derive_performance_id()` hashes the artifact and sorted segment
IDs, so a performance ID names one exact membership. Each performance ID is
one `audio_song_assertion` object. Same-ID snapshots are its revisions in
source order. A successor linked by `supersedes_performance_id` has a
different membership, so it asserts about a different excerpt and is a
different assertion object (ADR 0044). The predecessor link is retained as
source evidence; it neither retracts nor revises the earlier object.

**3. Only `reviewed` becomes an affirmative assertion.** A `reviewed` snapshot
supports a human assertion. `suggested` and `retracted` snapshots are not
proposed as claims; they stay in the bundle with an explicit not-proposed
disposition in the migration report. Because no retraction contract exists,
an assertion object whose snapshot history leaves `reviewed` (a later
`retracted` or `suggested` snapshot) is unresolved as a whole. Admitting only
its earlier reviewed revisions would present a withdrawn assertion as
standing. A segment whose `review_state` is missing or not `reviewed` remains
unresolved, as the readiness note already requires.

**4. Boundaries round to the nearest sample of the probed rate.** V2 stores
no sample rate, so migration reads it from the pinned original's content in a
recorded derivation run that pins the probing tool and its version (ADR 0006).
Each boundary is `round_half_even(seconds × rate)`. The claim retains the
source seconds and names this rule, so the conversion is auditable rather than
silent. A selection stays unresolved when the content has no single constant
sample rate, cannot be probed, or yields `start ≥ end` or an end past the
artifact's last sample. The rounding bound is half a sample; the six-decimal
source rounding (1 µs) is far below one sample at any rate V2 recorded.

**5. Machine matches use a closed input model; V2 scores do not migrate as
claims.** An `audio_song_match` payload is closed over: method identifier and
version; score with its scale and distance direction; the compared selection
revision pin; the corpus selection revision pins; pinned parameters; and the
pinned run that produced it. No other input may influence the score. A V2
cover-song score whose run and inputs cannot be recovered from the bundle
stays there, unresolved. Matches are recomputed under the contract and cite
their actual run. The four-contract release in ADR 0040 is unchanged.

## What this does not decide

- **Multi-segment performances.** A performance whose membership spans more
  than one segment chain (for example a recorder rollover) has no single
  migrated selection to assert about. Whether migration composes a
  multi-piece selection, and what names it, is open. Until then such
  performances are unresolved.
- Retraction and assessment contracts, which would let decision 3 carry a
  withdrawn assertion instead of leaving it unresolved.
- Per-kind current rules, the exact JSON Schemas and context fragments, and
  the release's version number (ADR 0046).

## Alternatives considered

- **Every segment record is its own selection.** A correction would look like
  a new excerpt, contradicting ADR 0044.
- **A whole performance supersession chain is one assertion object.** The
  object's selection would change identity across revisions.
- **Carry `suggested` and `retracted` as non-affirmative claims.** This needs
  a state field and a retraction representation that no contract defines.
- **Widen boundaries outward, or convert only exact offsets.** Widening adds
  audio the operator did not select; exact-only leaves nearly every real
  selection unresolved, because operator boundaries were never sample-aligned.
- **Defer the match contract, or migrate V2 scores as reported.** The first
  amends accepted ADR 0040; the second records scores whose inputs cannot be
  pinned.
