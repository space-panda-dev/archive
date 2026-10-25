# 0120. A session's ensemble is an explicit assertion

Status: proposed (2026-10-25). No contract, permission or view in this record
is accepted. Blocks the review client's removal of its recording-list side
table; the [prerequisite plan](../proposals/review-client-prerequisites.md)
separates this choice from already accepted capture work.

## Context

The review client needs a recording date and band from World rather than its
legacy preview index. A session name is already the label of a
`recording_session` Entity (ADR 0048). Renaming it changes that Entity's
revision, not its identity or which ordered parts it contains.

None of the existing relations answers which ensemble a session is of:

- `band_membership` relates a person to an organisation, with partial dates
  (ADR 0078); a member can play in several bands on the same day.
- Session participation says who took part, not what band was rehearsing
  (ADR 0095). Guests and substitute players make inference unreliable.
- `artist_credit` says whom a selection is presented as by (ADR 0048), and
  can differ between performances in one session.
- The relay Circle's optional band is client context, not archival evidence
  (ADR 0088). A different login or relay must not change a recording's facts.
- A session label is a name; parsing it cannot create evidence (D-8).

Date is a separate prerequisite: accepted ADR 0107 specifies `capture_start`
and its session reader rule. This proposal does not replace that decision.

## Proposed decision

Record the operator's assertion that a session is of an ensemble as a Claim
pinning one `recording_session` Entity revision and one `organisation` Entity
revision. Preserve conflicting assertions and judgments under INV-7. Do not
copy a relay setting or derive the assertion from membership, participation,
a Work's origin or a performance credit.

The operator must settle these choices before a contract is drafted:

1. Is the relation a session fact, or are ensemble credits per selection the
   only desired fact? For a mixed evening with two bands, can one session
   have two ensemble assertions, or should its list show no single band?
2. Is asserting it operator-only, or may a member assert their own claim
   about a readable session? Permission is separate from the view choosing
   what to display (ADR 0094).
3. Should the recording-list view return every surviving assertion, or use
   a named rule selecting the operator's? Unknown and unresolved conflict
   must be explicit; it must not silently choose the first organisation.
4. What inherited predicate and contract name express this relation? Confirm
   its meaning under ADR 0012 before adding vocabulary or a bundle.

An accepted version must answer all four and give exact cardinality,
reference targets, supersession and reader rules. Until then the recording
list reports that the association is unavailable. A presentation placeholder
is not an assertion stored by World (INV-10).

## Consequences

A correction is another attributable proposal to Door. The session's name,
identity and ordered sources remain independent. The same reader applies to
old and newly captured sessions. No regrouping or reordering interface is
introduced by this work.

Implementation follows acceptance: an additive contract release and fixture,
Door validation, World projection and a permission-aware named view. Tests
must cover unknown, two bands, a guest in another band, a renamed session,
and a member who may read the session but may not assert the relation.
