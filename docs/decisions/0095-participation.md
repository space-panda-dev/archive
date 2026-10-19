# 0095. Participation is a contribution at session scope, and only the operator counts it

Status: proposed (wave 1, drafted 2026-10-19)

Friends and bandmates are getting logins of their own ([ADRs 0092](0092-internet-exposure.md) to [0094](0094-peoples-writes.md)),
and three applications will read World: the rehearsal review app, the
lead-sheet songbook, and a sample app. Once more than the operator annotates,
a view has to say whose word comes first. The operator wants someone who was
in the room for a rehearsal to count for more, on that rehearsal, than a
friend who only listened to it, and both to count for more than a machine.
Nothing in an archive can say who was in the room:

- A rehearsal is a `recording_session` Entity with its parts listed in order
  by `archive.claim/session_parts`
  ([ADR 0048](0048-review-app-write-contracts.md)). Neither names a person.
- `archive.claim/contribution` version 1
  ([ADR 0078](0078-contributions.md)) credits a person at five scopes
  (Work, release, recording, span, channel). A session is none of them: a
  `recording` credit about one part Artifact says nothing about the other
  parts, and a session has many parts.
- `archive.claim/band_membership` version 1 says who was in a band, and
  when, but not who came to a given rehearsal: members miss rehearsals, and
  friends sit in.
- ADR 0082's first current rule ranks by `person`, `document`, `machine`
  for one chosen person. Every other person's claim is listed and never
  chosen; there is no notion of a participant.

Anyone with the default annotate grant may assert a contribution ([ADR 0094](0094-peoples-writes.md)).
If participation counted whoever asserted it, a member could raise their own
standing by saying they were there.

The operator decided the following on 2026-10-19 (wave 1 decisions W1.9,
and the tiers of W1.4); this record states them as given.

- **W1.9.** "This person took part in this session" is a contribution (a
  performer role) at a new **session** scope. Only participation the
  operator asserted or affirmed counts for a participant tier, so nobody
  promotes themselves. Band membership on the date may suggest
  participants; suggesting is not counting.
- **W1.4 (tiers).** Grants decide what a login may propose. How much an
  annotation counts is decided only by named views and current rules (ADR
  0082), e.g. tiers operator > participant > other member > machine. No
  weight is ever stored on a claim (INV-8).

## Decision

**`archive.claim/contribution` version 2 adds the `session` scope.**
Version 1 is installed by specification 0.0.9 and its `scope` is a closed
set, so a new scope is a new version. Version 2 is version 1 with one more
scope; every version 1 rule holds for the other five, and version 1 stays
installed for the claims that cite it. A `session` claim:

- pins exactly one `session`, an Entity revision of kind
  `recording_session` (ADR 0048);
- pins exactly one `contributor`, an Entity revision of kind `person`. A
  band does not sit in a room; each of its members does. (Version 1 allows a
  contribution with no contributor; a session claim needs one, since "someone
  played here" is no participation);
- has a `role` whose `type` is one of the MusicBrainz artist-recording
  performer types `performer`, `instrument`, or `vocal`, with their
  attributes, instruments, and vocals as ADR 0078 checks them. `instrument`
  still needs at least one instrument. A role with no instrument is
  `performer`: "took part";
- may keep `text` and `credited_as`, and the asserter fields `run`,
  `document`, `document_url`, `retrieved_at`, `locator`, and `confidence`,
  exactly as version 1;
- never gives `spans`, `sample_rate`, `channel`, or `alignment`.

```json
{
  "agent": "urn:uuid:…(the operator's person)",
  "payload": {"scope": "session",
              "role": {"type": "instrument",
                       "instruments": ["https://musicbrainz.org/instrument/<MBID of bass guitar>"]}},
  "refs": [
    {"role": "contributor", "id": "urn:uuid:…(a bandmate's person)", "revision_id": "sha256:…"},
    {"role": "session", "id": "urn:uuid:…(a Paper Lanterns rehearsal)", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

One person on two instruments is still one claim; two people are two claims
(ADR 0078). A person who played in only some of the session's parts is
credited at `recording` or `span` scope on those parts, as today.

**Session scope refines nothing, and nothing refines it.** ADR 0078's
resolved view takes containment only from what the claims themselves pin. A
`recording` credit about a part Artifact does not pin the session; that the
Artifact is a part of the session is a third claim, `session_parts`. The
resolved view is unchanged: a session claim is *finest* and stands beside
the part-level credits. The participation view below lists those beside it.

**A participation is a claim; counting it is a view.** A **participation**
is a head contribution version 2 claim with scope `session`. Any asserter may
make one: the operator, a member about themselves or someone else, a machine
(a later speaker detector, which pins its run). A participation **counts**
for a session, at a snapshot, when every one of these holds:

1. it is the head revision and its own agent has not withdrawn it (ADR
   0081's standing, as every view reads it);
2. the active operator's standing on that exact revision is not
   `rejected`;
3. its agent is the active operator, or the active operator's standing on
   that exact revision is `affirmed`.

The active operator is the person the head operator appointment names, as
World reads it today (`world/record_views.py`'s `_operator`). A person whose
participation counts for a session is a **participant** of it. Rule 3 is
what keeps anyone from promoting themselves: a member's own claim is a
suggestion of equal standing to anyone's until the operator affirms it. The
affirmation pins a revision, so an asserter who revises an affirmed claim
(to name another person, or another session) stops counting until the
operator affirms the new revision. Another person's rejection never
uncounts it: W1.9 makes counting the operator's alone. It is still listed,
as `disputed`, beside the count.

**Band membership suggests, and never counts.** A **suggested participant**
is a person a band membership places in the band on the session's date and
whom no participation counts. Suggestions are computed in the view's answer,
never stored, never claims, and have no effect on any tier. A client may
offer one to the operator as a draft participation to assert; asserting it
is what counts. A membership suggests when the operator asserted it (as
ADR 0088's band rules read memberships), its band is one the view asks
about, and its `from` and `to` partial dates may include the date: the
earliest day `from` can mean is on or before it, and the latest day `to` can
mean is on or after it, or it has no `to` and is not `ended`. A membership
that is `ended` with no `to` is suggested with `uncertain` true.

No record holds a session's date. The session's label carries one as text
from the capture filename (ADR 0070), but a label is a name and never
identity or evidence (D-8), and P-5 forbids substituting a capture time.
So the date is a view parameter, and without one nothing is suggested
(Open 3).

**`archive.view/session_participants` version 1.** Its question is "who
took part in this session, who says so, and who might have?". Its subject is
`{"session": <object id>}`, a `recording_session` Entity. Its parameters
are:

- `on`: an RFC 3339 full date, or null (the default): the session's date,
  for suggestions only;
- `bands`: 0 to 16 distinct organisation object identifiers; by default
  every band in which a participant holds an operator-asserted membership
  on `on`.

Its answer states `operator` (the person it counted for) and:

- `participants`: per person, the roles their counting participations
  name, and `counted_by`, each counting claim with `how` (`asserted by the
  operator` or `affirmed by the operator`, naming the judgment revision);
- `claimed`: every participation that does not count, with its agent and
  `why` (not affirmed by the operator, or rejected by the operator);
- `disputed`: counting participations someone other than the operator has
  rejected, with the judgment revisions;
- `suggested`: per person not counted, the band, the membership revision,
  `why`, and `uncertain` where it applies;
- `part_credits`: the head contributions at `recording`, `span`, or
  `channel` scope whose subject is an Artifact listed by the session's head
  `session_parts` claims (directly, or through a span's or channel's
  `source`), so a reader sees who is credited on which part;
- `withheld`, as every view states it (ADR 0082).

```json
{"operator": "urn:uuid:…",
 "participants": [{"person": "urn:uuid:…(a bandmate)", "roles": [{"type": "instrument", "instruments": ["…"]}],
                   "counted_by": [{"claim": "sha256:…", "how": "affirmed by the operator", "judgment": "sha256:…"}]}],
 "claimed": [{"claim": "sha256:…", "person": "urn:uuid:…(a friend)", "agent": "urn:uuid:…(the friend)",
              "why": "asserted by the friend; the operator has not affirmed it"}],
 "disputed": [],
 "suggested": [{"person": "urn:uuid:…", "band": "urn:uuid:…(Paper Lanterns)", "membership": "sha256:…",
                "why": "member of the band on 2026-05-03 by the operator's membership claim"}],
 "part_credits": []}
```

**A tier by asserter is computed per question, never stored.** An
**asserter tier** places a claim's agent, for a claim about a given subject,
in one of four tiers, highest first:

| Tier | The claim's agent is |
|---|---|
| `operator` | the active operator's person |
| `participant` | a person who is a participant of a session the claim's subject belongs to |
| `member` | any other person |
| `machine` | a model or process Entity, or the claim pins a `run` |

A claim's subject belongs to a session when an Artifact whose samples it
counts (its `source`, or its selection's or occurrence's source Artifacts)
is a part in that session's head `session_parts` claim. A claim about a
Work, or about audio in no session (a commercial album), has no session, so
for it nobody is a participant and the tiers are `operator`, `member`,
`machine`. Participation is per session: a bandmate who played on Monday is
a participant for Monday's recordings and a member for Tuesday's.

A current rule that ranks by asserter tier:

- states the step in its rule, by name and version (S-18), for example "of
  the people's claims left, the highest asserter tier", and computes the tier
  from the snapshot on every request;
- gives in each choice its `asserter_tier` and a `why` that names the
  participation claims and operator judgment that placed the asserter;
- treats claims within one tier as equal: two participants who disagree are
  a `conflict`, as ADR 0082's step 4 reports, never a sum or a vote;
- lists every other claim beside the choice, as ADR 0082 requires.

Nothing is written to a claim's payload, envelope, or projection to say its
tier. If the operator withdraws an affirmation, the next answer ranks the
same claim lower, and no record changes. Like ADR 0082's tiers, these rank
evidence for one question as the operator chose them; they say nothing about
belief in general, and a viewer who wants another ranking asks for another
rule (INV-8). How the asserter tier sits beside ADR 0082's `document` tier,
and which rule adopts it first, is Open 1; this record changes no served
view's rule.

**Grants and capacity are a different question (ADR 0094).** A session-scope
contribution is a contribution, which the default annotate grant covers
(W1.6), so any login whose audiences can read the session may propose one,
naming itself or anyone. The grant decides only that it may be proposed.
Turning a person's grant off does not uncount a participation the operator
affirmed, since counting reads the operator's records, not the asserter's
grant; and being a participant grants nothing. Permission is not weight
(W1.4).

**Audiences ([ADR 0093](0093-audiences.md)).** The view, like every view, runs on the records the
caller may read. The operator's participations and judgments are the
operator's own records, which ADR 0088 never discloses by inheritance; a
member reading the view sees a participant only when the operator disclosed
the claims that count them, and `withheld` says what was kept. A member's
asserter tiers are therefore computed from what they may read, and may
differ from the operator's (Open 4).

**Door's part.** A specification release installs contribution version 2
with the profile rules ADR 0078 already needs (required roles that depend on
`scope`, a target kind per role, a role vocabulary per scope) and the
`kinds` keyword of ADR 0048; no new Door keyword is needed. The member draft
check (ADR 0088's list, and ADR 0094's grant for contributions) adds
contribution version 2. Door checks no counting: counting is a view.

**World's part.** World projects a session claim as it projects any
contribution: a PROV qualified attribution whose subject is the session
Entity revision, with `arch:contribution_scope` `session`. The projection
shape's scope list gains `session`, which is a projection revision. World
registers `archive.view/session_participants` version 1 in the one view
registry, and holds the counting rule and the asserter tier in
`world/participation.py`, standard library only, beside
`world/contributions.py`.

**The relay's part.** No new route and no new refusal code. The view is
served by `/world/view` from the registry (ADR 0082). A draft session claim
pins the session Entity revision, which must be readable to the caller
(`not_disclosed` otherwise, ADR 0088).

## Consequences

- The operator can say, once per rehearsal, who was there, and a bandmate
  can say it about themselves; the bandmate's word counts once the operator
  affirms it.
- Views can rank a participant's annotation of a rehearsal above a
  listener's, per session, with the reason stated, and without a weight
  anywhere in the archive.
- Self-promotion is impossible by construction, and so is promotion by a
  friend: only the operator's assertion or affirmation of an exact revision
  counts.
- Band membership saves the operator typing: the view suggests the band,
  and the operator affirms or asserts. Until a session date is recorded,
  the client must supply the date.
- A session claim and part-level credits sit side by side; nothing derives
  one from the other.
- Members see counts only for what the operator disclosed, so the operator
  has to disclose participations with the session for members' views to
  rank by them.
- No invariant changes. INV-7 keeps every participation, counted or not;
  INV-8 is kept by naming the rule and its question and storing nothing;
  INV-24 by `claimed`, `disputed`, and `withheld`.

## Alternatives considered

- **Participation as its own contract.** It would duplicate the contributor,
  role, asserter, and source fields of a contribution, and two readings of
  "who played" would need reconciling. W1.9 makes it a contribution.
- **Session as a `recording` scope target.** A `recording_session` is not a
  recording: it has many parts and no samples, and ADR 0078's recording-scope
  readers (track credits, the part join) would mistake it for one.
- **Count every participation.** Anyone could promote themselves (W1.9).
- **Count a participation the participant asserted and a bandmate
  affirmed.** Two friends could promote each other; counting stays the
  operator's.
- **Count band members on the date.** Members miss rehearsals and friends sit
  in; a suggestion that counted would make the band roster a tier.
- **Store a participant flag, or a tier, on a person or a claim.** S-18 and
  INV-8: a stored tier is a weight, and stale the moment an affirmation is
  withdrawn.
- **Make participation an operator transition.** Who was in a room is a fact
  people can be wrong about and disagree over, which is a claim (ADR 0005);
  a transition is for governance. The operator's judgment of the claim is the
  governance step W1.9 needs.
- **Session containment in the resolved view.** It would need the resolved
  view to read `session_parts`, a third claim, against ADR 0078's rule that
  containment comes from what the claims pin; `part_credits` gives a reader
  the same picture without changing that rule.

## New nouns

**Session scope** (an extension of ADR 0078's Scope, not a new noun in
itself). It credits a contribution to a whole `recording_session`. It does
what no other scope does: name a person on every part of a session at once.
It owns no verb beyond *assert*, enforces no invariant, is a value of an
existing claim's payload, owns no state outside claims Door admits, and is a
detail of the contribution contract.

**Participation.**
1. It names a person as having taken part in one session, which neither
   band membership nor a part-level credit says.
2. No verb of its own; it is asserted (Annotation's *assert*).
3. None; INV-7 keeps it like any claim.
4. It is code inside an existing concept: a contribution with scope
   `session`. The noun names that case so views and people can talk about it.
5. No; it is a claim Door admits.
6. A detail: a kind of contribution claim.

**Participant.**
1. It is the one thing that gives a person a tier above other members, for
   one session, and only by the operator's word.
2. None; World *projects* it as part of a view.
3. It keeps INV-8's rule that belief is stated per question, not stored.
4. It is code inside World's views: a computed set, never a record.
5. No; it is recomputed from each snapshot.
6. A projection (part of a view's answer).

**Suggested participant.**
1. It turns band membership on a date into a prompt for the operator,
   without counting.
2. None; World *projects* it.
3. None.
4. It is code inside the participation view.
5. No.
6. A projection.

**Asserter tier.**
1. It ranks a claim by who asserted it, relative to the claim's subject,
   for a rule that names it; ADR 0082's tiers rank by kind of evidence for
   one chosen person.
2. None; World *projects* it inside a rule's answer.
3. It keeps INV-8 and S-18: a tier is computed per named rule and never
   stored.
4. It is code inside World's views (`world/participation.py`), a function
   a rule calls; the noun names a value of ADR 0082's Tier.
5. No.
6. A detail of current rules.

## Touch points

- `world/contributions.py`: `CONTRIBUTION` (add version 2), `SCOPES`,
  `SCOPE_ROLES` (`session`: `{"session": (1, 1)}`), `COMMON_ROLES` (the
  contributor count becomes 1 for `session`), `ROLE_TYPES` and `role_type`
  (the three performer types for `session`), `check_contribution` (the
  session subject, the person-only contributor, no span or channel fields),
  `_within` and `resolve` (session refines nothing; unchanged in behaviour,
  documented).
- `world/participation.py` (new): the counting rule, `participants`,
  `suggested`, `asserter_tier`, and the session an Artifact belongs to,
  reading `world/judgments.py`'s `standing` and `world/record_views.py`'s
  `_operator`.
- `world/record_views.py`: `VIEWS` and `HANDLERS` register
  `archive.view/session_participants` version 1, with its subject and
  parameter checks (it may reuse `_on`'s date check).
- `world/world.py`: `_project_music_claim`'s contribution branch (the
  `subject_role` map gains `session`), `_targets` (unchanged), and
  `PROJECTION_REVISIONS` (6: 7 becomes 6: 8).
- `world/projection/6/revision.ttl`: `wsh:CreditClaim`'s
  `arch:contribution_scope` `sh:in` list gains `"session"`.
- `world/circle.py`: `MEMBER_CONTRACTS` gains contribution version 2, and
  `world/grants.py` (new, ADR 0094) maps it to the annotate ability.
- `relay/relay.py`: no change beyond the registry, which `_view_answer`
  already reads through `run_view`.
- `spec/`: a release installs the contribution version 2 bundle.
- `docs/vocabulary.md` (Credits; Review and views): session scope,
  participation, participant, suggested participant, asserter tier.

## Open for the operator

1. **Where does the asserter tier sit beside ADR 0082's tiers, and which
   rule adopts it first?** D4.2 orders `person` (the chosen person),
   `document`, `machine`, and never chooses another person's claim. W1.4's
   tiers rank asserters. Recommendation: change no served rule now; the
   first adopter is a new version of `chords_current`, served beside version
   1, that after the chosen person's tier and before documents chooses
   other people's claims by asserter tier `operator` then `participant`,
   and still only lists `member` claims; span annotations get an ordering
   view in the review app's wave.
2. **Does an operator affirmation made through Cloudflare (member
   capacity, [ADR 0092](0092-internet-exposure.md)) count?** A record does not say which capacity sent
   it, and the judgment's agent is the operator's person either way.
   Recommendation: it counts. Counting changes what views rank, never what
   is disclosed or granted, so it is not the operator capacity W1.3 keeps on
   the tailnet. If the operator wants it tailnet-only, the relay would have
   to refuse an operator's judgment of a participation in member capacity,
   which is a grant rule for ADR 0094. This depends on ADR 0092's question 3
   (capacity is not recorded on records).
3. **Where does a session's date come from?** No record holds it, and the
   label is not evidence. Recommendation: the view takes `on` from the
   client for now; a later decision adds a session date claim (from the
   capture file's start, `unknown` when absent), alongside wave 2's capture
   work, and the view's default reads it.
4. **Should participation claims be readable to an audience whenever the
   session is?** Otherwise a member's tiers differ from the operator's.
   Recommendation: no exception to ADR 0088; the operator discloses
   participations with the session, and a client can offer to include them
   in the approval.
5. **May non-performers be session participants** (the engineer who
   recorded a rehearsal, a producer in the room)? W1.9 says a performer
   role. Recommendation: performer types only now; others are credited at
   `recording` scope on the parts, and widening the scope's role list is an
   additive later change.
6. **What tier does a claim whose agent is an organisation take** (a
   band-adopted lead sheet, ADR 0084)? Recommendation: `member`, since a
   Work has no session; revisit if a band ever asserts about a recording.
7. **Whose affirmation counts after operator succession?** Recommendation:
   only the active operator's; a former operator's affirmations stop
   counting until the new operator affirms, and the view says so in
   `claimed`.
8. **May a sensitivity limit select "the rehearsals I play in" by
   participation** ([ADR 0018](0018-people-in-the-archive.md), and an
   [open question](../open-questions.md#claims-and-provenance) this record
   does not settle)? Recommendation: yes, in the decision that settles limit
   selection (ADR 0094's area): a limit only narrows (INV-26), so any
   participation naming the person should select, counted or not; counting
   matters only for ranking. ADR 0094's test for which subjects a member may
   limit accepts only participation the operator asserted or affirmed (its
   question 2); the decisions do not settle which.
