# 0019. Publication approval is a transition claim

Status: accepted

ADR 0010 calls publication approval an assessment. That conflicts with the
later record model: an assessment judges whether a claim is true, while an
approval exercises governance authority and every archival transition is a
claim of a transition kind (ADR 0003, ADR 0005, S-8). The V2 port cannot encode
both statements.

## Decision

**A publication approval is a claim of kind `publication_approval`.** It is a
governance transition, not an assessment. It records the person authorizing it,
its destination or operator-named exposure class, and references pinned to the
exact revisions it covers. A publication claim cites the approval claim that
authorized it. Withdrawal is a separate transition claim and does not rewrite
either approval or publication.

This decision supersedes only ADR 0010's statement that approval is recorded
as an assessment. ADR 0010's destination, origin, rights, fresh-approval, and
withdrawal rules remain unchanged. The specification already requires every
transition to be a claim (S-8), so acceptance needs no specification change.
The approval claim is the evidence of a governance transition required by ADR
0003. It is not the independent permission or operational record rejected by
ADR 0010.

This does not require a separate approval screen or a synchronous send. A
review interface may record the approval as part of committing the reviewed
revision, and an unattended publisher may act on it later. The approval pins
the resulting revision, not the revision the interface opened before the edit.
The interface reports approval separately from actual publication.

A kind-based publication default is policy: it says which reviewed work ought
to be published. It does not replace the person's approval required by INV-20.
When review applies that default, the review action materializes an approval
for the exact resulting revision and destination. An explicit hold-back does
not erase an earlier publication; withdrawal remains its own transition.

## Alternatives rejected

- **Keep approval as an assessment.** It would make a judgment of evidence do
  governance work, collapsing the distinction ADR 0005 exists to preserve.
- **Put approval fields directly on the publication claim.** It would erase the
  separately attributable act of approval and make one approval covering
  several exact revisions or a destination class impossible to reference.
- **Treat approval as operational state.** Loss would remove the authority for
  a recorded publication, so approval is evidence rather than disposable
  bookkeeping.
