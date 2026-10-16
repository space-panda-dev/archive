# 0051. Door dispatches publication transitions

Status: accepted

Specification 0.0.5 installs `archive.transition/publication_approval` and
`archive.transition/publication_withdrawal` (ADRs 0019, 0048). ADR 0028's
dispatch table contains only initialization, retire, and destroy. Every
other transition kind fails as unsupported, so Door can validate a
publication approval but cannot commit one, and the review app could not
replace V1's publish control. The operator asked on 2026-10-14 for this to be
settled before the shadows start.

## Decision

**Both kinds join the dispatch table.** `door transition` accepts the exact
installed `(id, version, digest)` tuples for `publication_approval` version 1
and `publication_withdrawal` version 1, under ADR 0028's manifest, lock,
expected-heads, and single-commit rules. `door admit` keeps refusing them.
The dispatch table and the installed catalog stay enumerated against each
other in tests.

**The authorizer is the active operator.** As for retire and destroy, the
`authorizer` reference must pin the current revision of the person Entity
that the active operator appointment names (INV-20 requires a person). The
envelope `agent` may be someone else, such as the review app's user or a
model that drafted the approval (ADR 0005: author and authorizer are
different facts).

**An approval covers current revisions.** Every `covers` reference must pin
the current head of its object, and `expected_heads` must name each of them.
This follows ADR 0019, where an approval pins the revision that results from
the review, not one the interface opened earlier. Approving a superseded
revision is refused.

**A withdrawal names one live approval.** `withdraws` must pin an existing
`publication_approval` revision that no earlier withdrawal names. A second
withdrawal of the same approval is refused, and a withdrawn approval stays in
history unchanged.

**Hold-back is recorded the same way.** A `hold_back` decision passes the
same checks as an approval. It does not withdraw an earlier approval: that
is a separate withdrawal transition (ADR 0019).

**Door does not judge the destination.** `destination` is recorded exactly.
Exposure classes are policy (ADR 0016), and the policy language is not yet
ported, so Door does not check a destination against policy. A publisher that
acts on an approval remains responsible for INV-20's treatment of an
undeclared destination.

## What this does not decide

The publisher's own record of an actual send, the derived "is this approved
for that destination now" view, and policy-driven default approvals
(ADR 0019's kind default) remain open.

## Alternatives considered

- **Let any person Entity authorize.** INV-20 asks for the operator's
  governance authority, not anyone the archive records.
- **Allow approving a superseded revision.** The approval could then cover
  content the reviewer never saw in its final form.
