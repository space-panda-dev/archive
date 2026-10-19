# 0066. Machine-suggested spans are admitted claims

Status: accepted; presets amended by [ADR 0091](0091-talk-needs-level.md) (talk needs level; a
`studio` preset)

A fresh recording reaches World as a bare Artifact. The machine's suggested
spans, its boundary candidates, exist only in the V2 system's preview cache, in
float seconds, with no schema and no run. A review client therefore has
nothing to adjust. ADR 0044 said "Boundary candidates and unaccepted review
drafts remain outside the archive". The operator decided on 2026-10-16 that
suggestions should be kept as machine claims, which ADR 0006 leaves to
policy; this record is that policy until the policy language exists.

## Decision

**`archive.claim/span_suggestions` version 1 holds one run's suggested spans
for one source.** It pins exactly one `source` Artifact revision, exactly one
`stream` (`archive.claim/audio_stream` version 1, ADR 0065) about that same
Artifact revision, and exactly one `run` (ADR 0064). Its payload has:

- `sample_rate`: equal to the stream claim's `sample_rate`;
- `spans`: 1 to 10,000 objects of `start_sample`, `end_sample`, and `class`,
  where `0 <= start_sample < end_sample <= sample_count`, spans are in order
  and do not overlap (each starts at or after the previous one's end), and
  `class` is one of `music`, `talk`, `quiet`, or `unknown`.

Door checks every rule, including the bound against the pinned stream's
`sample_count`.

**A suggestion is never a selection.** It accepts nothing and names no
excerpt. A person (or another reviewer) accepts audio by proposing an
`audio_selection`, which may use a suggestion's edges or any other sample.
A suggestion is never current, and a newer run's suggestions do not supersede
an older run's; both remain, each with its run.

**ADR 0044 is amended.** Boundary candidates may be admitted under this
contract. Unaccepted review drafts still stay outside the archive.

**One detector, recorded.** The detector in `enrich/` works in integer
samples, uses one fixed signal set, and fails when a signal cannot be
computed instead of dropping it. Its preset is its `parameters` Artifact and
its decoder and analysis libraries are the run's `software`.

## Consequences

- World projects each span with its class, so a client can draw candidates
  and snap edges to them; the class supplies Music or Talk for a kept span.
- Each detector run adds one run and one suggestions claim per source. At
  tens of spans a recording, the cost is a few kilobytes.
- Edges inside a candidate are a client decision. The archive already
  accepts any sample bound in a selection.

## Alternatives considered

- **Keep suggestions in a derived file beside the archive.** World would have
  to read files that are not evidence, and a client would need a second read
  path with no provenance.
- **One claim per span.** Tens of records per recording, each repeating the
  same source, stream, and run.
- **Suggestions as draft selections with a "suggested" state.** ADR 0047
  keeps review states out of claims; a state field would make every reader
  filter.
