# 0024. Retire and destroy records keep decisions, not execution state

Status: accepted; note added 2026-10-19 (lineage and containment
dependents, [ADR 0098](0098-content-lineage.md) and
[ADR 0101](0101-daw-projects.md), both accepted by the operator,
2026-10-19)

Retire and destroy are settled as person-initiated transition claims, but their
minimum record contracts are not. In particular, a destroy record must explain
the content decision and every dependent decision without turning changing
copy status into immutable payload. A working archive also needs a retire
contract; defining destroy alone would still leave `door transition`
unsupported.

## Decision

The copied specification ships claim contracts
`archive.transition/retire`, version `1`, and
`archive.transition/destroy`, version `1`. The contract citation names the
transition kind and version; the payload does not repeat them.

Both claims have one pinned `author` reference to a person entity and one
pinned `authorizer` reference to the active operator's person entity. The two
may name the same person but remain separate facts. `agent` is the author who
asserts the transition; the writing program remains `by`. A machine
recommendation may be referenced as evidence, but it never becomes the
initiating person.

### Retire

A retire payload is the closed object:

```json
{"reason": "operator-supplied reason"}
```

It has exactly one `retires` reference, pinned to the current revision of the
object leaving the collection. Recording the claim retires the object and
leaves every one of its records in place. An identical retry changes nothing.

### Destroy

A destroy payload is the closed object:

```json
{
  "content": {"digest": "sha256:...", "size": 123},
  "reason": "operator-supplied reason",
  "external_survivors": []
}
```

It has one or more pinned `destroys` references naming the retired artifact
revisions that refer to those exact bytes. Door refuses the transition if an
unretired object still refers to the content.

Every dependent artifact with content of its own that is reachable from the
destroyed content under the installed contracts has exactly one pinned
reference in one of two roles:

- `dependent_destroy` means that dependent content is included in the same
  transition through its own destroy claim in the atomic batch;
- `dependent_source_gone` means the dependent content remains, with the loss
  of its source recorded.

No reachable dependent may appear in both sets or neither. Claims and
assessments without bytes remain as history and require no disposition.

`external_survivors` lists every outside copy known when the transition is
recorded. Each closed row contains a non-empty `destination`, a `basis` of
`disclosure` or `verified_copy`, and the RFC 3339 `observed_at` time supporting
that basis. When archival evidence exists, a pinned `external_survivor`
reference names it, normally a disclosure or copy-observation claim. A survivor
never blocks destroy or deletion completion; the snapshot prevents "destroyed"
from quietly meaning "gone everywhere". A newly discovered survivor is new
evidence and does not edit the original transition.

Recording the claim makes the content destroyed for archival purposes
immediately. The destroy record does not store managed locations, deletion
attempts, or `pending`/`complete` fields. Those states are derived from the
destroy claim and current git-annex state. Door and apply refuse every later
attempt to place content whose destroy claim is authoritative.

If policy contributed to either transition, S-23 additionally requires the
claim to pin the policy revision, rules that fired, and facts matched. The
policy grammar, stable rule names, and matched-fact representation are still
open. Until they are accepted, a policy-caused retire or destroy must fail as
unsupported. Direct person-initiated transitions do not answer that question
and may use these contracts.

This decision defines content destruction only. Information erasure and Git
history expungement remain unsupported (ADR 0004).

## Why these records are sufficient

The envelope and role references retain specification, program, author,
authorizer, and recording time. The payload retains the exact digest, size,
human reason, and outside-survivor snapshot S-21 and S-22 require. Pinned
references preserve the objects considered, every explicit dependent choice,
and durable evidence for survivors where it exists. Mutable execution state
stays outside the record, preserving location independence and quiet history
(INV-5, INV-6).

The predecessor provides evidence for atomic transition batches and exact
pins. Its decision shape and retire-by-supersession are rejected by ADR 0003
and ADR 0015, so neither is retained.

## Alternatives rejected

- **A decision shape or bare supersession.** Every transition is a claim, and
  retirement is not revision lineage (S-8, ADR 0003).
- **Store author or authorizer in the payload.** Envelope provenance and pinned
  role references are normative; duplicating them can disagree (S-11,
  ADR 0005).
- **Store managed locations or deletion status in destroy.** They change as
  work proceeds and would make location changes rewrite evidence (INV-6).
- **Automatically cascade.** Storage destruction and information erasure have
  different dependency choices; ADR 0004 requires each decision explicitly.
- **Use unpinned dependent or survivor references.** A later revision could
  change what the transition appears to have considered.
- **Guess policy provenance.** S-23 requires rules and matched facts, while
  their representation is still an open question.

## Note, 2026-10-19: lineage and containment dependents

The rule above is unchanged; what it reaches grows. "Reachable from the
destroyed content under the installed contracts" gains its first
contracts: [ADR 0098](0098-content-lineage.md)'s lineage claim (the outputs
made from the destroyed content) and [ADR 0101](0101-daw-projects.md)'s
project snapshot (the snapshot packs that hold or rebuild it), computed
together as one set of dependents with the dispositions above. A whole
snapshot pack holds the destroyed bytes, so it accepts only
`dependent_destroy`. Until a specification release installs those
contracts, the specification's D-9 refusal of `dependent_destroy`,
`dependent_source_gone`, and `external_survivor` references stands, and
Door refuses them as unsupported; the release replaces that refusal with
the lineage and containment dependents.
