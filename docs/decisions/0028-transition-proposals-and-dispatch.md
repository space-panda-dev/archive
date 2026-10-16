# 0028. Door dispatches atomic transition proposals by exact contract

Status: accepted

Retire and destroy contracts do not by themselves define how Door receives a
transition, selects its effect, binds the state it checked, or commits a batch
of dependent destructions. Calling a generic claim writer would leave the
transition effect unreachable; accepting a contract name without its bytes
would dispatch on weaker evidence than the record cites.

## Decision

`door transition --proposal <directory>` accepts a metadata proposal directory
with the same safe-path and retained-byte rules as admission. It contains
`manifest.json` and the record drafts named beneath `records/`. Its closed
manifest is:

```json
{
  "protocol": "archive-transition/1",
  "records": ["records/0001.json"],
  "identities": [{
    "object_id": "urn:uuid:...",
    "namespace": "archive.transition/retire",
    "name": "retire_7f3a"
  }],
  "expected_heads": [{
    "object_id": "urn:uuid:...",
    "revision_id": "sha256:..."
  }]
}
```

All arrays are required and non-empty for a transition proposal. `records`
orders the draft transition claims. `identities` contains exactly one ADR 0026
allocation for every new transition object. `expected_heads` names every
existing object whose revision or derived state Door relies on, including each
transition subject, dependent, survivor-evidence object, author, and authorizer;
it also contains a null expectation for every new transition object.

Door loads and verifies every cited contract bundle before dispatch. Transition
dispatch keys on the exact tuple `(contract id, version, digest)`, not a payload
string. The initial dispatch table contains only the installed
`operator_initialization`, `retire`, and `destroy` bundles. Initialization alone
may invoke the first kind. `door transition` supports retire and destroy.
Every other transition kind fails loudly as unsupported even if its JSON Schema
would validate.

`door admit` refuses a claim citing a transition contract. This makes the
transition handler, its authorization check, and its state effect impossible to
bypass through the generic admission path. The dispatch table and the installed
specification contract catalog are enumerated against each other in tests, so a
new writer cannot be added without a reachable handler and validator.

Under one archive lock Door:

1. retains and validates all proposal bytes, identities, exact contract
   citations, references, and expected heads;
2. verifies the author, authorizer, active operator, and legal pre-state for
   every transition;
3. computes all revision identifiers and the resulting retire/destroy state;
4. checks that every `dependent_destroy` artifact has exactly one matching
   destroy draft in the same batch and every destroyed content item has all
   required retired subjects and dependent dispositions;
5. rechecks proposal bytes and expected heads; and
6. atomically writes the records and makes one Git commit.

Transition drafts may reference existing revisions and earlier drafts whose
revision identifiers Door can compute without a cycle. A forward reference or
cycle is refused. Destroy drafts do not need to reference one another:
`dependent_destroy` pins the dependent artifact, and Door matches its content
to the sibling destroy draft.

Before applying the proposal's original expected-head tests, Door checks
whether every computed revision already exists as one previously committed
batch. If so, it returns them in manifest order and writes nothing. A partially
present set, or—when no complete retry exists—a changed contract, authorizer,
expected head, or state precondition is a refusal. `door transition --dry-run`
performs the same pure validation and revision computation but writes no record
and reports that authorization and state must be rechecked at commit.

Policy-caused transitions remain unsupported until the policy provenance
fields required by S-23 are defined. Publication, disclosure, reclaim,
succession, policy revision, and every other transition remain unsupported
until accepted contracts and handlers exist. This proposal does not decide
which additional transitions the archive will support.

## Why exact dispatch matters

The contract tuple is already the immutable definition the claim cites. Using
it for dispatch anchors the complete chain from installed contract, through
validation, to the only handler that may change derived archival state. The
single lock and commit bind the decision to the heads and legality Door checked
(INV-2, INV-5, INV-12).

The predecessor provides evidence that ordered metadata batches, expected-head
fences, pure canonicalization, and one-commit writes prevent partial and stale
transitions. Its command names and decision semantics are not retained.

## Alternatives rejected

- **Dispatch on a payload `kind`.** It can disagree with the contract citation
  and lets a weaker string select an authoritative effect.
- **Let `door admit` write transition claims.** The record would exist without
  the authorization and state transition it claims.
- **Validate or commit one transition at a time.** Dependent destruction could
  leave an authoritative partial decision.
- **Accept any schema-valid transition contract.** Schema validation cannot
  supply Door's state-effect handler.
- **Use current heads discovered after validation.** The operation would act on
  state different from the proposal and violate the plan-binding principle.
- **Add a daemon or transaction database.** One local lock, immutable files,
  and one Git commit provide the required boundary without new authority.
