# 0122. An admission can require its referenced objects' heads to stay unchanged

Status: accepted (operator, 2026-10-25). Amends
[ADR 0023](0023-admission-and-staging.md) and specification D-4.

## Context

A review can create a title or artist-credit claim about an existing selection
without revising that selection. Its admission's `expected_heads` contains the
new claim objects only. Door accepts exact references to historical revisions,
which is necessary for evidence, but this means a review made against an old
selection can still be admitted after another writer moves the selection's
edges. Rechecking World in the app cannot close the race between that read and
Door's write. The review cutover requires a stale save to write nothing.

The existing admission manifest requires `expected_heads` to contain exactly
the drafted objects. Adding a referenced selection there is malformed; making
all references require heads would instead forbid legitimate historical pins.
The precondition belongs to Door under its archive lock (INV-2), separately
from the meaning of the admitted claim.

## Decision

Add an optional `expected_reference_heads` array to `archive-admission/1`.
Absent means `[]`, preserving existing proposals. `expected_heads` retains its
existing exact-drafted-objects rule. A guarded review can contain:

```json
{
  "expected_reference_heads": [
    {"object_id": "urn:uuid:00000000-0000-4000-8000-000000000001",
     "revision_id": "sha256:0000000000000000000000000000000000000000000000000000000000000000"}
  ]
}
```

This fragment extends the existing manifest; its other fields remain required.
Each guard must name an existing object and a non-null exact revision, using
the existing object and revision identifier grammar. Guard objects must be
distinct, must not be drafted in this admission, and must occur in the drafts'
explicit references. Every explicit pin to a guarded object must agree with
its guard. Missing, duplicate, conflicting, unrelated or drafted-object guards
are malformed; historical references remain valid when not guarded. This does
not change a contract's rule for which claim counts as current.

For a batch that would introduce new revisions, Door compares every guard with
that object's head in its authoritative admission snapshot. A missing object
or changed head refuses the entire batch as `expected_head_stale`, without
revealing another revision identifier and without writing records, commits or
content-location entries. A dry run checks the same guard but reserves nothing.

The comparison happens under the same archive write lock as admission planning
and commit. Door rechecks it with the retained proposal bytes and ordinary
expected heads immediately before committing, including after content
verification. It must not rely on a previous dry run or on World. A change
between dry run and commit therefore refuses atomically; a writer cannot
interleave between the final check and Door's commit.

### Identical retry

Keep D-4's existing complete-batch retry identity: the computed record revision
set already admitted together, not a new idempotency token. Validate guard
structure and reference agreement first. Then detect a complete previously
committed batch before testing guards against present heads, just as Door does
for `expected_heads`. Return its stored revisions with `retry: true` and write
nothing even if a guarded object's head has since moved. A partial revision set
never qualifies. A changed record batch must satisfy every guard afresh.

Guards are operational admission preconditions, not new archival evidence.
Their bytes do not enter a record's revision identifier, and admitting them
must not create a separate record or history event (INV-5).

### Compatibility and caller responsibilities

Publish this additive manifest support in specification 0.0.12, coordinated
with its other accepted additions. Do not alter existing contract bundles or
require every historical admission to carry guards. Existing callers omitting
the field retain their current behavior. Older Door versions reject the new
field in the closed manifest; they must never ignore it. The relay carries the
manifest unchanged through both dry-run and commit routes.

A caller that requires stale-review protection must include the chosen guards
in its durable proposal, dry-run that exact guarded batch, and send the same
manifest to commit. An older archive's refusal is a blocker, not permission to
strip guards and retry. The review app guards the existing selection it is
reviewing and any other existing objects whose displayed revisions formed its
decision. It does not blindly guard historical provenance references. A rename
that revises the session Entity still uses ordinary `expected_heads`.

This change covers admission only. Transition's existing expected-head rules
and authorization stay unchanged. It grants no member access, adds no World
refresh route, and introduces no lead-sheet UI.

## Required proof

Use the real Door with synthetic records, and the relay on a scratch archive:

- Dry-run a new claim guarding selection revision A, admit a competing revision
  B, then commit the original reviewed batch: `expected_head_stale`, unchanged
  record set, commit count and content-location state.
- The same stale proposal's dry run refuses without a write; no guarded batch
  is admitted merely because a prior dry run succeeded.
- A valid guarded multi-record admission commits atomically. Its identical
  retry writes nothing, including after a guarded target changes later.
- A changed batch with stale guards and a partially present batch are refused.
- Existing unguarded proposals and intentionally historical references retain
  their behavior. Invalid guard shapes, duplicates, null pins, unrelated
  objects, drafted objects and conflicting reference pins are refused.
- Retained-manifest mutation and the existing write-lock/final-recheck tests
  cover the new preconditions. Relay refuses nothing differently by caller
  identity and preserves Door's refusal code and World refresh result.

## Alternatives considered

- **World preflight only:** cannot prevent another writer between read and
  commit, so it cannot satisfy the cutover gate.
- **Extra rows in `expected_heads`:** changes the established drafted-object
  invariant and makes ordinary write expectations ambiguous.
- **Require every reference to be a head:** breaks valid historical evidence
  and makes unrelated provenance edits block review saves.
- **A new record/claim for the precondition:** records an operational check as
  evidence and changes retry identity without adding archival meaning.
