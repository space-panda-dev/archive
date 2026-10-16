# 0030. Transition batches retain their authorization order

Status: accepted

The transition proposal manifest is ordered, but the manifest is operational
input and is not archival evidence. Once Door writes the records, Git stores a
set of record paths; a reader cannot recover the order in which Door checked
pre-state and applied the effects. This is unsafe for a batch whose later
transition relies on the retire or destroy effect of an earlier transition.
Authorization and state replay must therefore use durable evidence, not the
disposable proposal directory.

## Proposal

Use the existing pinned-reference mechanism to make a transition batch a
linear chain. The transition record at position zero has no
`transition_predecessor` entry in `refs`. Every later transition record has
exactly one such entry, whose `revision_id` is the P-3 revision computed for
the immediately preceding transition draft in the same proposal. It is never
a `supersedes` reference. The reference must resolve to a claim dispatched by
an accepted retire or destroy contract tuple, and it must point to an earlier
draft, never to a record from an older batch. A one-record batch is the
degenerate chain with no predecessor.

The manifest remains ordered input, but Door MUST require that its record list
is the same order described by this chain. The chain is copied into the
committed records, so a reader can identify the root and walk every successor
without retaining the proposal directory. The chain is also part of the
revision input; changing the order changes the affected transition revisions.
Door MUST reject a missing, duplicated, branching, cyclic, cross-batch, or
out-of-order predecessor chain.

The immutable version `1` bundles do not change. A specification release that
implements this decision ships version `2` of the retire and destroy bundles.
Those schemas add `transition_predecessor` to their permitted reference roles,
and their context fragments map that owned term exactly to
`arch:transition_predecessor`. The initialization bundle remains version `1`
and does not permit the role. Door accepts version `1` retire or destroy only
for a one-record batch; an ordered multi-record batch requires version `2` for
every record. Version `1` records remain readable and retain their original
meaning.

The role is structural batch evidence; it does not replace `destroys`,
`dependent_destroy`, `dependent_source_gone`, `retires`, or any other
contract-owned relation. The atomic Git commit remains the batch boundary. A
complete retry must satisfy ADR 0028's existing complete-batch rule: the same
computed revisions (which include the exact predecessor pins) must all have
been introduced by that one commit. Chain equality does not weaken any
non-retry identity, authorization, expected-head, or state check.

This is the smallest design that preserves the existing exact-reference
model. A reader can replay authorization and pre-state in chain order, while
the expected-head fence still binds the whole batch to the state Door checked
before the first effect.

## Alternatives rejected

- **Keep only the manifest order.** The proposal directory is not archival
  state and may be deleted after admission; the committed records then lose
  the order needed to replay dependent pre-state.
- **Add an integer order field to every transition payload.** This duplicates
  batch structure inside each contract-owned payload, permits gaps and
  duplicate positions, and makes every transition contract carry semantics
  that the common reference model already expresses.
- **Add a transition-order field to the envelope.** It expands the closed
  normative envelope with a transition-only field and forces every reader and
  revision implementation to learn a new top-level shape.
- **Infer order from recording time, filenames, or Git tree order.** `at` is
  recording time rather than an ordering claim, clocks can tie, and Git tree
  paths are sorted by revision rather than by authorization sequence.
- **Infer order from incidental reference or object allocation order.** Those
  relations do not necessarily connect sibling destroys and do not prove the
  order in which Door checked their legal pre-state.
- **Store a sidecar batch index, database row, or daemon state.** Operational
  state is not durable archival evidence and would violate the single-lock,
  immutable-record boundary.

Acceptance requires a new specification release containing the version `2`
retire and destroy bundles; it must not rewrite the accepted `archive/0.0.1`
resources. It should add fixtures for a valid two-record chain, a missing
predecessor, a predecessor to an existing older record, a fork, a cycle, and a
manifest/chain order mismatch. Moving an existing archive to that release
remains bounded by the open specification-migration question.
