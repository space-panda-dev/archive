# 0094. People's writes: grants are transitions, and members set their own limits

Status: accepted (operator, 2026-10-19); proposed in wave 1 and its open
questions settled the same day

[ADR 0088](0088-bandmates.md) lets a bandmate with a relay login assert
claims in their own name. What they may send is one fixed list,
`MEMBER_CONTRACTS` in `world/circle.py`, the same for every member and
changed only by a code release. Three applications will now read World and
propose to Door: the rehearsal review app (cut, name, release), a lead-sheet
songbook, and a sample app whose cuts are made in the review app. Friends and
bandmates will log in through the internet
([ADR 0092](0092-internet-exposure.md), proposed) into named audiences
([ADR 0093](0093-audiences.md), proposed). That exposes four gaps:

- **Who may write what is configuration, not evidence.** Nothing in the
  archive says that a person was allowed to cut, or when that changed. The
  operator cannot answer "could Sam admit files in March?" from the records, and
  turning a person's writes off is a code or configuration change that leaves
  no trace (ADR 0005 keeps governance acts attributable).
- **Members cannot cut.** `archive.claim/audio_selection` is not in
  `MEMBER_CONTRACTS`, so a bandmate cannot mark the excerpt the sample app is
  built from; the operator must cut for them and the cut is then the
  operator's.
- **Members cannot add content.** ADR 0088 refuses every Artifact from a
  member, and the relay stages only the operator's documents
  ([ADR 0085](0085-documents-lyrics-rights.md); `check_stage_request` in
  `world/documents.py`). A friend who wants to deposit a sample library or a
  phone recording must hand the files to the operator.
- **Members cannot set their own limits.** ADR 0088 keeps
  `archive.claim/sensitivity` the operator's to send, so a bandmate's limit
  ([ADR 0018](0018-people-in-the-archive.md), INV-26) is still typed by the
  operator in their name, and the
  [open questions](../open-questions.md#the-archive-contract) ask whether a
  bandmate may assert their own.

There is a fifth risk, which the decisions below rule out: once permissions
are per person, it is tempting to let a "trusted" person's word count for
more. INV-8 forbids that, and [ADR 0082](0082-views-and-current.md) already
puts every ranking in a named view.

The operator decided the following on 2026-10-19 (wave 1 decisions W1.4 to
W1.7); this record states them as given.

- **W1.4 Permission is not weight.** Grants decide what a login may propose.
  How much an annotation counts is decided only by named views and current
  rules (ADR 0082), e.g. tiers operator > participant > other member >
  machine. No weight is ever stored on a claim (INV-8).
- **W1.5 Grants are archive evidence.** "This person may annotate / cut /
  upload, over these audiences" is an operator transition read from World;
  the relay enforces it; turning one on or off is an auditable transition,
  not a configuration edit.
- **W1.6 Default grants.** A new login may, over what its audiences can read:
  annotate (span annotations, judgments, titles, contributions) and cut
  (`audio_selection`) by default. Upload (admitting content) is off by
  default and granted per person. A cut carries its author as agent.
- **W1.7 Members set their own limits.** A member asserts sensitivity limits
  on content that records them (ADR 0018, INV-26). Limits the operator
  entered on their behalf stay valid; the member may lift or tighten them.
  This answers the open question about bandmates asserting their own limit.

The operator settled this record's open questions on 2026-10-19 (round 4
answers R4.2, R4.3, R4.5, R4.7 and R4.8; R4.11 settles the rest as
recommended; round 5 answer R5.2 settles the last). Wave 2 amended it the
same day, before installation (round 6 answers R6.10 and R6.11). The
decision below states the settled choices; the list at the end records
them.

## Decision

### A grant is a transition, not a claim

**`archive.transition/write_grant` version 1** records the operator turning
one ability on or off for one person over named audiences. It is a
governance transition, like a publication approval
([ADR 0019](0019-publication-approval-is-a-transition.md)), for three
reasons:

- **INV-16 and ADR 0005.** Who may propose what is a governance fact: it
  causes a consequence (the relay admits or refuses a person's writes) and
  makes no one's claim truer. Governance authority is the operator's, and a
  transition records author and authorizer separately and has Door check the
  authorizer at commit. A claim records only an asserter; anyone may assert
  any claim, so "Sam may admit files" asserted by Sam would be admitted as
  evidence and every reader would have to filter by agent to find the ones
  that count. That filter would be the authority, outside Door.
- **[ADR 0028](0028-transition-proposals-and-dispatch.md).** Transitions are dispatched by
  exact contract to a handler, and `door admit` refuses any record citing a
  transition contract. So a grant cannot be written through the generic
  admission path, by a member or by a client bug: it reaches Door only
  through `door transition`, which the relay answers for the operator alone
  (ADR 0088), and only over the tailnet (W1.3,
  ADR 0092).
- **W1.5** calls it an operator transition, and asks that turning one off be
  as auditable as turning it on. A transition is kept forever (INV-7), and
  its successor says when the change happened by Door's recording time.

A grant is not a policy revision. Policy declares desired state for copies,
retention and disclosure ([ADR 0007](0007-policy.md)); a grant authorizes
proposals. So INV-17 ("policy only adds") does not govern it, and a later
grant may turn an ability off, as a publication withdrawal ends an approval.
A grant cites no policy revision until the policy language exists, the
same answer the open question gives for circle disclosure (R4.11).

**The contract.** References: exactly one `grantee`, a person Entity
revision; exactly one `authorizer`, a person Entity revision, which Door
requires to be the active operator. The envelope `agent` is the author: the
person who proposed the change, normally the operator. Payload:

- `ability`: one of `annotate`, `cut`, `admit` (below; W1.6 calls the third
  "upload", and the [naming table](../vocabulary.md#naming-discipline) says
  *admit*; the operator confirmed `admit`, and applications may label it
  "Upload", R4.7);
- `decision`: `allow` or `deny`;
- `audiences`: 1 to 16 distinct audience names, each 1 to 200 characters,
  exactly as ADR 0093's audience transitions name them (a name is never
  changed or reused, so it identifies one audience);
- `reason`, optional, 1 to 2,000 characters.

```json
{
  "agent": "urn:uuid:…(the operator's person)",
  "payload": {"ability": "admit", "decision": "allow",
              "audiences": ["band"], "reason": "Sam deposits phone recordings of rehearsals"},
  "refs": [
    {"role": "grantee", "id": "urn:uuid:…(Sam's person)", "revision_id": "sha256:…"},
    {"role": "authorizer", "id": "urn:uuid:…(the operator's person)", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

**Door's part.** A release installs the bundle and adds `WriteGrant` to
Door's `transitionDispatchTable`. The handler checks: the authorizer is the
active operator; the grantee is a person Entity and not the active operator,
since a self-grant authorizes nothing new (R4.2, below); the payload is
closed and its audience names distinct; every audience name is the name of a
live audience ([ADR 0093](0093-audiences.md)) in the state the transition
commits against, so Door refuses a grant that names no live audience (R4.5),
as it refuses a circle approval to one; and no two grants in one batch name
the same grantee, ability and audience. The grantee need not have a login
yet, so the operator can prepare a grant before a person's first sign-in
(R4.11). A grant has no effect on Door's own derived state: Door records it,
and World and the relay read it. A grant that named a live audience stays
admitted when that audience later ends, and grants nothing there.

**One act per change, latest wins.** There is no separate withdrawal
contract: turning an ability off is a `deny` grant. For each grantee,
ability, and audience, the **effective grant** is the head `write_grant`
naming them with the latest Door recording time, the rule ADR 0085 already
uses for a person's latest sensitivity claim. Where none names them, the
**default** applies: `annotate` and `cut` allowed, `admit` denied (W1.6).
The default is part of the named view below and changes only with a new
version of it, never by configuration. A grant is never superseded or
retired to undo it; the history of grants is the audit trail W1.5 asks for.

### What each ability lets a member send

The relay already checks a member's drafts (`check_member_draft`). It gains
one more test: each draft must fall under an ability, and the caller must
hold an effective `allow` for it over some audience they belong to in which
every revision the draft pins is readable (ADR 0093's per-audience readable
set; before ADR 0093, the circle is the one audience). Otherwise the draft
is refused **`not_granted`** (403), a new code under
[ADR 0058](0058-door-refusal-codes.md)'s rule, naming the ability.

**The operator's internet login (R4.2).** Over the internet the operator's
person is a member ([ADR 0092](0092-internet-exposure.md)) and reads the
union of the audiences that list it, plus its own records whose pins are
readable in one of them (ADR 0093, R5.1). The view's rule gives that
login all three abilities, in member capacity, over those audiences; no grant
names the operator, and Door refuses one that does. It still sends only what
member capacity allows: no transition, no operator-only contract.

| Ability | Default | What it admits |
|---|---|---|
| `annotate` | allowed | Claims and assessments about readable things: everything now in `MEMBER_CONTRACTS` except `sharing_consent` (judgments, span annotations, selection kind and title, contributions and band memberships, links of a take to a Work, and the music-fact claims of ADRs 0077 to 0086), a contribution at [ADR 0095](0095-participation.md)'s session scope, and an origin claim (ADR 0096) about readable content, never `own_capture` (R4.4) |
| `cut` | allowed | `archive.claim/audio_selection` version 2, pinning readable Artifact revisions, with the caller as its envelope `agent` (W1.6); and [ADR 0098](0098-content-lineage.md)'s lineage claim, which W1.12 gives to anyone with a cut grant |
| `admit` | denied | Artifacts whose content the relay staged for the caller, with the claims that describe them in the same batch: a document source (ADR 0085) and [ADR 0096](0096-origin-of-recordings.md)'s origin claim |

**Wave 2's additions (R6.10).** The proposed wave-2 records (ADRs 0099 to
0107) add contracts to these abilities. This one amendment lists every
addition, so the ability of each contract is stated in one place; each
record's own table agrees with it.

| Ability | Gains |
|---|---|
| `annotate` | [ADR 0099](0099-samples.md)'s `sample_form`, `sample_sound` and `sample_tag` version 1, and the versions of `selection_kind`, `selection_title`, `artist_credit`, `song_occurrence` and `performance_work` that accept selection version 3; [ADR 0103](0103-voice-capture.md)'s `voice_note` version 1 about a readable Artifact or selection; [ADR 0105](0105-notebook.md)'s `notebook_link` version 1; [ADR 0106](0106-set-lists-and-clocks.md)'s `set_list` version 1, with the caller as agent; [ADR 0107](0107-multi-device-and-live.md)'s `capture_start` version 1 about a readable Artifact, and `song_occurrence` version 2, so a member can link a rendition to a Work |
| `cut` | ADR 0099's `audio_selection` version 3 beside version 2, and the lineage of a render the caller chose |
| `admit` | a chosen sample render staged by ADR 0099's stage route; with staged content in the same batch, [ADR 0100](0100-packs-and-collections.md)'s `source_path` version 1, ADR 0103's `voice_note` version 1 and ADR 0107's `capture_start` version 1; a bounce ([ADR 0101](0101-daw-projects.md)) or a kept practice take (ADR 0107) as audio with its origin |

Everything else wave 2 adds stays the operator's or a machine's: Entities
(including `collection` and `daw_project`), membership claims, project
snapshots, `session_parts`, capture alignments, `midi_stream`, a clock's
meter claim, speaker spans, catalogue correspondence version 2, and rights.
Two wave-2 writes join the list of what no grant gates, below.

**What no grant gates.** Some writes are a person's own say, not a
permission, and a `deny` cannot take them away:

- a member's own sensitivity limit (below);
- a writer's `sharing_consent` (ADR 0088), which only ever withholds or
  allows the operator's sharing of their own work;
- a `withdrawn` judgment of the member's own claim
  ([ADR 0081](0081-judgments-and-annotations.md)), so a member whose
  `annotate` was turned off can still take back what they said;
- a person's own voice consent ([ADR 0104](0104-transcription-and-speakers.md)),
  and their `rejected` judgment of a speaker span naming them (R6.10).

Everything ADR 0088 kept the operator's stays so: every transition, rights,
work origin, Entities, machine provenance, correspondence, and capture
alignment.

**A revoked ability is not retroactive.** A `deny` stops future proposals.
What the person already asserted stays admitted, keeps its agent, and is
shown as before (INV-7). A view never drops or discounts a claim because its
asserter's grant changed: grants say what a login may propose, never what a
claim is worth (W1.4).

### Permission is not weight

No grant, ability or audience appears in any claim's payload, and no named
view reads grants to rank claims. Where a view ranks by who said something,
it does so by its own stated rule, per question, as
`archive.view/chords_current` does (ADR 0082). The tiers W1.4 names
(operator, then participant, then other member, then machine) are such a
rule; ADR 0095 (proposed) defines who is a
participant, and a view that adopts the tiers is a new version of that view.
A person with `admit` is not thereby a participant, and a person with no
grants is not thereby lower.

### Admitted content goes through staging, then Door

A member with an effective `admit` allow may use the relay's staging route,
which today answers only the operator (`check_stage_request`). The flow is
ADR 0085's, unchanged except for who may call it:

1. The client sends the bytes to the relay. The relay checks them, adds them
   to its own staging git-annex repository, and records them as staged for
   that caller (`Documents.staged_for`, already keyed by agent).
2. The client sends an ordinary admission. The relay passes it only when
   every content row is bytes it staged for this caller (`check_contents`,
   which today refuses every member), the caller holds `admit`, and every
   draft passes the member checks above.
3. Door admits it under [ADR 0023](0023-admission-and-staging.md): it fetches
   the key from the relay's staging remote, verifies fixity and size, and
   commits. Nothing is archival before that commit; staged bytes that are
   never admitted are removed after seven days, as now.

**Who is named where.**

- The **Artifact's envelope `agent`** is the member: they asserted it, and
  ADR 0056's rule that every draft names the caller's agent holds. Its `by`
  is the client program.
- **ADR 0096's origin claim** (proposed), in the same batch and asserted by
  the member, says where the content came from; when the member is not its
  maker, it names them in the depositor role (W1.10).
- **The custodian is the operator,** and no new record is needed to say so:
  everything Door admits is in the custody of the archive's active operator
  ([ADR 0009](0009-copies-and-custody.md), INV-3), who is named by the
  archive's `operator_initialization` transition and by any succession after
  it. Custody first: the friend's material is held in the operator's archive,
  under the operator's policy.

**A member's admitted content reaches no one else until the operator says so.** The member
reads their own records already (`readable_by`), so they read what they admitted.
Others read it only through a publication approval to their audience, which
stays the operator's (ADR 0088). Beyond the circle, an Artifact with no
origin is others' work (W1.13), so admitting it widens nothing.

Members admit Artifacts, not Entities: a recording session for an admitted
phone recording is still the operator's to create (R4.11).

**Staging transport and scope (R4.11).** Today's staging takes 64 MiB of
base64 JSON and only document types; audio and sample libraries are larger.
Members' staging uses a streamed staging route with a size cap from
configuration, accepts audio and document media types only, and stages
Artifacts only. A member's admission starts no enrichment run; the
operator's next run picks it up.

### Members set their own limits

A member may send **`archive.claim/sensitivity` version 1** (ADR 0085)
through the relay, under these rules, which amend ADR 0088's "nor can a
member assert `archive.claim/sensitivity`":

- **Their own.** Its envelope `agent` is the caller. A limit is always the
  person's own (ADR 0018); a member never sends one in another's name.
- **On content that records them.** Every `subject` must **record the
  caller**: it is
  - the session of a head participation ([ADR 0095](0095-participation.md)'s
    `session`-scope contribution) naming the caller as contributor, or an
    Artifact that session's head `session_parts` lists, where the
    participation was asserted by the operator, affirmed by the operator, or
    asserted by the caller themselves, and the active operator's standing on
    that revision is not `rejected` (R4.3);
  - the subject of a head contribution at `recording`, `span` or `channel`
    scope (ADR 0078) naming the caller as contributor, where the
    contribution was asserted by the operator, affirmed by the operator, or
    asserted by the caller themselves, and the active operator's standing on
    that revision is not `rejected` (R5.2);
  - content the caller deposited: the subject of a head origin claim
    ([ADR 0096](0096-origin-of-recordings.md)) that names the caller in the
    `depositor` role, which the operator asserted or affirmed, or which
    arrived in the same admission as that content, and which the operator
    has not rejected (R4.8, R5.3);
  - content the caller recorded: the subject of a head origin claim that
    names the caller in `captured_by`, under the same condition as a
    depositor's (R5.3), so a member can limit their own voice note
    ([ADR 0103](0103-voice-capture.md)) (R6.11);
  - content the caller is heard speaking in: the `source` of a head speaker
    spans claim ([ADR 0104](0104-transcription-and-speakers.md)) with a
    span naming the caller that is not set aside, under the same condition
    (R6.11); or
  - a subject of one of the caller's own head sensitivity claims, so that a
    limit the operator entered on their behalf can always be revised by
    them.

  A subject that fails the test is refused `not_granted` with the reason.
  The test is for limits only (R4.3, R5.2, R6.11). A member's own participation,
  or their own credit on a recording, span or channel, selects what they may
  limit, because a limit only narrows (INV-26); it does not make them a
  participant or a credited contributor, which still needs the operator's
  assertion or affirmation (W1.9, ADR 0095). Someone else's unaffirmed claim
  that the caller played does not select. The operator can reject a
  self-asserted participation or credit; a rejection stops it selecting new
  limits, but a limit the member already set stays theirs and binds until
  they lift it (INV-26), subject only to the operator's one-destination
  override.
- **Lifting and tightening.** Because ADR 0085's rule takes each person's
  latest sensitivity claim about an object as their limit, a member lifts
  or tightens simply by asserting a later claim, or by superseding their own
  (ADR 0088's rule that a member supersedes only their own agent's records
  admits it, since a limit entered on their behalf already names them as
  agent). Limits the operator entered for them stay valid until then (W1.7).
  The operator's later claim still cannot lift a member's limit (INV-26), and
  the strictest limit of all persons still wins, so a member's `CLEAR` lifts
  only their own limit and never another's.
- **Pinning what their own limit hides.** A member's own `RED` makes the
  object private, so it leaves the circle's readable set, and
  `check_member_draft` would then refuse a draft that pins it. A sensitivity
  draft from the caller may therefore pin a subject revision that one of the
  caller's own head sensitivity claims pins, readable or not. The member
  learns nothing new by this: the identifiers are in their own limit, which
  they already read (`readable_by` serves a member their own records). The
  content stays unserved while any limit forbids it. This is the least W1.7
  needs (R4.11).

The member's limit binds exactly as the operator-entered one does: it caps
the readable set, publication, and every unattended act (ADR 0018). The
operator's one-destination override (INV-26) is unchanged.

**A depositor's limits bind like a recorded person's (R4.8).** What a friend
deposits may not record them: a sample library they bought, or a recording
of someone else. The operator decided that a depositor's limits on what they
deposited bind exactly as the limits of a person the content records. That
changes INV-26's wording and ADR 0018's; [ADR 0096](0096-origin-of-recordings.md),
which owns the depositor role, proposes the amendment text. The depositor clause of
the test above takes effect with those amendments, never before them.

### World and the relay

- **Projection.** World projects `write_grant` like any transition. Nothing
  about grants is stored outside the records Door admits.
- **`archive.view/write_grants` version 1**, a named view (ADR 0082): for a
  `person` (a member may ask only about themselves; the operator about
  anyone), each ability's effective grant per audience they belong to, with
  the deciding grant revision or `default`. Its defaults are W1.6's.
- **`archive.view/person_limits` version 1**: for a `person` (members:
  themselves), their head sensitivity claims, and the readable objects that
  record them under the test above, so a client can offer "set my limit" on
  exactly what the relay will accept.
- **The relay's `archive` block.** `caller` gains `grants`: for each ability,
  the audiences in which it is allowed, from the same generation the relay
  serves. A client hides what it cannot send; the relay still checks.
- **When a change takes effect.** The relay reads grants from World's
  selected generation, as it reads the readable set (`Access`). A grant the
  operator commits through the relay refreshes World before the answer
  (`Relay._door`), so it applies to the next request; one committed with
  operator tools applies from the next refresh. Door does not re-check grants:
  the relay enforces them (W1.5), and only the relay and the operator reach
  Door's socket (ADR 0054) (R4.11).

**The rules live in World until a release installs them.** A new module,
`world/grants.py`, would hold the effective-grant rule, the defaults, the
ability of each contract, and the records-them test, with only the standard
library, beside `world/circle.py`, so a client can check a proposal first.

## Consequences

- The operator can answer, from the archive alone, what a person could
  propose on a given date and who changed it, and turns off a person's
  writes with one recorded act.
- A bandmate cuts excerpts in their own name, and the sample app shows them
  beside the operator's, attributed. A friend deposits files without handing
  over a drive, and the operator still holds and governs them.
- A bandmate sets, lifts and tightens their own limit on the rehearsals they
  play in, which is what ADR 0018 wanted and could not enforce while the
  operator typed every limit. A false withdrawal now needs the bandmate's own
  login, not only the operator's honesty, though the relay's identity check
  (ADR 0092) is still the operator's machine attesting it.
- Who counts for more stays a per-question view rule, visible and
  versioned; a permission never leaks into belief.
- Door gains one transition handler and no new state. The relay gains one
  refusal code, two named views and an ability check on every member write;
  each check is a lookup in the generation it already loaded.
- A grant names audiences whose membership is itself evidence: ADR 0093
  records each audience as an operator transition, so who a grant reached on
  a given date can also be answered from the archive.

## Alternatives considered

- **A grant as a claim by the operator.** Evidence anyone may assert, with
  the authority in a reader's agent filter instead of Door's authorizer
  check (INV-16), and admissible through `door admit`, which ADR 0028 closes
  for governance acts.
- **Grants in relay configuration** (ADR 0088's `MEMBER_CONTRACTS` per
  person). W1.5 rejects it: a configuration edit leaves no trace in the
  archive.
- **A grant and a separate grant withdrawal**, as approval and withdrawal
  are. Defaults are on, so turning a default off needs a recorded `deny`
  anyway; one contract with `allow` and `deny` covers both directions.
- **One grant listing all of a person's abilities.** Each change would
  restate every ability, and an audit of one ability would read all of them.
- **Grants that carry a weight or tier.** W1.4 and INV-8.
- **Retroactive revocation** (hiding a revoked person's claims). It rewrites
  what views show from the past and makes a permission into a judgment of
  evidence (ADR 0005).
- **Members' limits queued for the operator to enter.** It is today's
  arrangement and what W1.7 replaces.
- **Any member may limit anything they can read.** W1.7 limits it to
  content that records them; otherwise any member could keep anything from
  publication.
- **Door enforcing grants.** Door would have to compute effective grants
  and audience membership to admit a member's record; W1.5 places
  enforcement in the relay, which already gates every member write.

## New nouns

**Write grant** (the transition).
1. It records, as evidence with an authorizer, what one person may propose;
   nothing else does.
2. None of its own: the operator's *authorize*, as for approvals.
3. INV-16 for proposal rights: only the operator turns them on or off.
4. It is a contract, inside Door's transition dispatch, not a concept.
5. Yes, as admitted records, like any transition; nothing else.
6. A record kind, not a subsystem.

**Ability** (`annotate`, `cut`, `admit`).
1. Names a set of contracts a person may propose as one unit.
2. None.
3. None by itself.
4. It is a closed payload value and a table in code (`world/grants.py`).
5. No.
6. A detail of the grant contract.

**Effective grant** (and its **default**).
1. Answers "may this person propose this, here, now?" from the records.
2. None; World computes it.
3. W1.4, by being read only for proposals, never for belief.
4. It is a computed view, `archive.view/write_grants`.
5. No: regenerable from the grants.
6. A projection.

"Records the caller" is a test inside the member checks, not a noun; depositor
is ADR 0096's role; audience is ADR 0093's.

## Touch points

Verified on 2026-10-19 to exist; a later implementation would change:

- `world/circle.py`: `MEMBER_CONTRACTS` (split by ability; add
  `audio_selection` version 2 and `sensitivity` version 1, and the sibling
  records' contribution version 2, ADR 0095, origin version 1 (under
  `annotate`, and under `admit` with staged content), ADR 0096,
  and lineage version 1, ADR 0098),
  `check_member_draft` (ability check, Artifacts under `admit`, the
  sensitivity subject exception), `readable_by` (unchanged rule, now relied
  on for own limits and admitted content).
- `world/grants.py` (new): effective grants, defaults, records-them test
  (operator-counted or self-asserted participation and part credits; a
  depositor or `captured_by` from an operator-counted or same-admission
  origin claim; a speaker span under the same condition), and the wave-2
  ability table (R6.10).
- `world/views.py`: `check_request` and `catalogue` gain
  `archive.view/write_grants` and `archive.view/person_limits`.
- `world/documents.py`: `check_stage_request` (members with `admit`),
  `check_document_admission` (staged content that is not a document).
- `relay/access.py`: `Access.check_drafts` and `Access.readable` read grants
  and audiences from the generation.
- `relay/served.py`: `stage_document` and `check_contents` admit a member's
  staged bytes under `admit`.
- `relay/staging.py`: `Documents.stage` and `Documents.staged_for`
  (already per agent; a streamed route, audio and document types, a size cap
  from configuration).
- `relay/relay.py`: `Relay.archive` (`caller.grants`), `Relay._door`,
  `check_proposal`.
- `relay/refusals.py`: `not_granted` (403); `relay/openapi.json`.
- `door/src/Archive/Door/TransitionDryRun.hs`: `TransitionKind`,
  `transitionDispatchTable`, and the transition validation for
  `WriteGrant` (live audience names, grantee not the operator).
- A specification release under `spec/` installing `write_grant` version 1
  and Door's rules for it; `docs/vocabulary.md` on acceptance.

## Settled by the operator (2026-10-19)

1. **Seeing what your own limit hides.** Accepted: a member may pin subjects
   of their own limits in a new limit, seeing identifiers, never content
   (R4.11).
2. **Who counts as recorded.** For limits only, participation naming the
   person that the operator asserted or affirmed, or that the person asserted
   themselves; the participant tier still counts only the operator's; the
   operator can reject a self-asserted participation (R4.3).
3. **Depositors' limits.** A depositor's limits bind like a recorded
   person's; ADR 0096 proposes the INV-26 and ADR 0018 amendment text (R4.8).
   A depositor counts only when the operator asserted or affirmed the origin
   claim, or it arrived in the same admission as the content (R5.3).
4. **Policy revision.** A grant cites none until policy exists (R4.11).
5. **Door re-checking grants.** No; the relay enforces (R4.11).
6. **The operator's own internet login.** It reads the union of the audiences
   that list it (R4.2); the view gives it all three abilities in member
   capacity, and Door refuses a grant naming the operator (R4.11).
7. **Staging transport and scope.** A streamed route with a configured size
   cap, audio and document types, Artifacts only, no enrichment run started
   by a member (R4.11).
8. **Grants before a login.** A grant may name any person Entity (R4.11).
9. **Door and audience names.** Door refuses a grant that names no live
   audience (R4.5).
10. **The ability's name.** `admit`; applications may label it "Upload"
    (R4.7).
11. **Self-asserted part-level credits.** Treated the same as R4.3's
    participation, for limits only: a person's own `recording`, `span` or
    `channel` contribution naming themselves selects that part for their
    limit, and the operator can reject it (R5.2).
12. **Wave 2's ability additions (from wave 2).** One amendment lists every
    contract ADRs 0099 to 0107 add to `annotate`, `cut` and `admit`, and
    the two ungated speaker writes (R6.10).
13. **`captured_by` and speaker spans select for limits (from wave 2).** An
    origin's `captured_by` and a speaker span naming the person select
    content for that person's own limits, under R5.3's condition (R6.11).
