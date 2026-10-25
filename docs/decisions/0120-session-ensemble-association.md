# 0120. A session's ensemble is an explicit assertion

Status: accepted (operator, 2026-10-25). The operator approved explicit,
operator-only session ensemble assertions separate from performance credits,
with unknown and conflict preserved. The [prerequisite plan](../proposals/review-client-prerequisites.md)
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

## Decision

`archive.claim/session_ensemble` version 1 is an operator-asserted Claim
with an empty payload, exactly one `session` reference to a
`recording_session` Entity revision and exactly one `ensemble` reference to
an `organisation` Entity revision. Each Claim asserts one pair. A session
can have several such Claims, including several ensembles; no single-band
restriction is imposed. The operator's person is the agent; the relay keeps
the contract outside its member allowlist. Door checks the operator agent
against its active operator, not a client-provided capacity string.

Use `arch:session_ensemble` for the ensemble reference and the existing
`arch:session` for the session reference. PROV association attributes an
activity to an agent; this Entity-to-organisation assertion is not an
activity association. A Music Ontology performance performer would change
the subject to a performance, and `artist_credit` is selection billing.
Those inherited meanings do not fit; ADR 0012 therefore permits a narrow
local relation without changing their meaning.

The recording-list reader follows the recording's session parts and returns
every head assertion of the active operator, with its exact session and
organisation pins and judgments. Superseding an assertion keeps its session
and agent; changing the ensemble is an explicit new assertion or revision,
never a mutation of the organisation. A session rename does not drop an
association: join by object identity while retaining the assertion's exact
revision pin. Historical pins remain available as evidence (INV-7).

No association means unknown. Several surviving ensembles are all shown;
no reader chooses one because it was returned first. Disputed assertions
and competing revisions stay visible with their judgments. Participation,
band membership, a relay's Circle band, a Work's origin and performance
billing never supply a missing association. The named reader must make
unknown and unresolved conflict explicit rather than choose a favourite.

The same relation and reader apply to old and newly captured sessions.
This decision adds no regrouping or reordering interface. Missing-session
reconstruction follows [ADR 0121](0121-legacy-session-backfill.md).

## Consequences

A correction is another attributable proposal to Door. The session's name,
identity and ordered sources remain independent. The same reader applies to
old and newly captured sessions. No regrouping or reordering interface is
introduced by this work.

Implementation requires: an additive contract release and fixture,
Door validation, World projection and a permission-aware named view. Tests
must cover unknown, two bands, a guest in another band, a renamed session,
and a member who may read the session but may not assert the relation.
