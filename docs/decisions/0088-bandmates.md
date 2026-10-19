# 0088. Bandmates read their circle and assert their own claims

Status: proposed

Only the operator can reach the relay. ADR 0056 maps each allowed tailnet
login to the person it acts as, refuses every draft that names anyone else,
and allows only the operator's own login, adding that "allowing anyone else
is a disclosure to them (ADR 0016) and needs its own decision". Every read is
raw SPARQL over World's whole selected generation, which is right for the
operator, whose data it is, and wrong for anyone else.

The application this release serves is for learning songs as lead sheets,
showing and correcting machine output, writing lead sheets of the operator's
own songs, and sharing them with bandmates. A bandmate who hears another
seventh in a co-written song should be able to say so in their own name, not
ask the operator to type it for them; the two views then stand side by side
([ADR 0005](0005-claims-review-and-authority.md)). Nothing lets them.

Two more things stand in the way of exercising it:

- [ADR 0060](0060-relay-interface-and-role.md) has clients refuse to commit
  unless the relay's role is `scratch`. The
  [cutover plan](../proposals/cutover-plan.md) needs commits to a `live`
  relay (condition 7), so as written the two cannot both hold.
- [ADR 0010](0010-publication.md) decides publication by the `origin` claim:
  the operator's own work publishes once approved, anything else needs a fresh
  approval. A song the operator wrote with bandmates is neither, no contract
  records origin, and the [open questions](../open-questions.md#the-archive-contract) ask
  how co-authored works are handled.

The operator decided the following on 2026-10-18; this record states it as
given.

- **D10.1.** "Bandmates get their own relay login bound to their own person
  agent. They read what's disclosed to their circle; their own claims,
  annotations and judgments are admitted under their name directly. Onward
  sharing and disclosure changes stay the operator's. Amends ADRs 0056 and
  0060."
- **D12.1.** "0.0.10 ships with the ADR 0060 amendment and is exercised on a
  SCRATCH relay with commits (cutover condition 6). The real cutover (live
  archive, commit mode, backups) is a separate approval right after."
- **D12.2.** "Sharing a co-written Work's lead sheet, stems or recording
  BEYOND THE BAND needs every co-writer's approval, recorded with the work's
  origin. Inside the band, the operator's circle disclosure suffices."
- **Implied.** Two people who disagree each assert their own claim; no
  weighting ranks them (ADR 0005, INV-8).

## Decision

**The relay's circle is a destination of class circle.** A seedbox with
accounts made for named people is ADR 0010's example of the circle class; a
relay with a login for each bandmate is the same thing. The relay's
configuration gains a `circle`:

```json
{
  "callers": {
    "operator-login": "urn:uuid:…(the operator's person)",
    "bandmate-login": "urn:uuid:…(a bandmate's person)"
  },
  "circle": {
    "destination": "band relay",
    "members": ["urn:uuid:…(a bandmate's person)"],
    "band": "urn:uuid:…(the band's organisation)"
  }
}
```

- `destination` (1 to 200 characters) is the destination's name, exactly as
  a publication approval names it.
- `members` lists, distinct, the person each bandmate's login acts as. Every
  caller other than the operator is a member and every member is a caller,
  so a login is never half-configured. The operator is never a member: the
  operator reads everything already.
- `band`, optional, names the band's organisation Entity. It is shown to
  clients and has no other effect; who is "in the band" for D12.2 is decided
  by claims, below, not by configuration.

A relay with no `circle` serves only the operator, as today, and refuses to
start if any caller is not the operator. Each member's person must be an
Entity of kind `person` in the archive; the relay checks this against World
when it starts. The circle's class is policy (ADR 0010); this record declares
it `circle` for the relay and for no other destination.

**A member reads what the operator disclosed to the circle.** The operator
discloses to the circle with what ADR 0010 and
[ADR 0019](0019-publication-approval-is-a-transition.md) already provide: an
`archive.transition/publication_approval` version 1 whose `destination` is the
circle's, whose `decision` is `approve`, and whose `covers` pin the exact
Claim revisions disclosed. A `publication_withdrawal` of it ends the
disclosure; a `hold_back` discloses nothing and ends nothing. No new contract
is needed, and the approval is committed by `door transition` before any
member can read what it covers, so ADR 0016's rule that every disclosure is
recorded before any content is sent holds: the approval is the record, and
like any disclosure record it may overcount, never undercount.

What a member may read, the **readable set**, is computed by World, never
stored, and recomputed with each snapshot:

1. **Covered.** Every revision a live approval to the circle covers, when the
   operator is its authorizer and the sharing gate below lets it reach the
   circle.
2. **What it pins.** Every revision those pin, through their references,
   transitively: a lead sheet's chords, meter, form, and lyric document; a
   chord claim's source Artifact, stream, and run; the person Entities its
   credits name. Showing a lead sheet shows its inputs
   (ADR 0084), so disclosing it discloses them. This
   is metadata; content bytes are the media routes' (below).
3. **Members' own.** Every claim or assessment whose envelope `agent` is a
   member, or the circle's band, when every revision it pins is readable.
   This is repeated until nothing more is added, so a bandmate's judgment of
   another bandmate's chord is readable when the chord is.

Every revision in the set must also be allowed to reach the circle by its
sensitivity and rights (ADR 0085): an
object whose furthest class is `private` is never readable, even when an
approval covers or pins it, and a record pinning it is shown with that
reference **withheld**, never dropped silently. A member always reads their
own records; another member reads them only while what they pin is readable.
The operator's own claims, judgments, and marks are never readable by
inheritance: the operator's correction of a disclosed chord is disclosed only
when an approval covers it. Withdrawing an approval shrinks the set at the
next snapshot; what members already read was disclosed and stays recorded as
such.

**A member reads through named views, not raw queries.** `/world/query`
answers the operator only. A member reads through the named views of
ADR 0082, which the relay filters to the
member's readable set: a view names an object, and a member who asks for one
outside the set is refused `not_disclosed`, whether or not it exists, so the
refusal reveals nothing. Media routes follow the same set: a member is served
a recording's listening copy, peaks
([ADR 0069](0069-derived-media.md)), or practice audio
(ADR 0089) only when that recording's
Artifact revision is readable.

**A member asserts their own claims, under their own name.** A member's
writes go to Door directly, as the operator's do: no queue, no operator
review before admission. ADR 0056's rule stands: every draft names the
caller's agent. A member's proposal is admitted only when every draft:

- is a Claim or an assessment (never an Artifact or Entity: content admission
  stays with operator tools, ADR 0056, and Entities are subjects the operator
  asserts);
- cites only member contracts: judgments (versions 1 and 2) and span
  annotations (ADR 0081); chord stacks, chord sequences, and meter layers
  ([ADR 0077](0077-chord-stacks-and-meter.md) and their 0.0.10 versions,
  ADRs 0079 and 0083); key regions, key sequences, and tuning (ADR 0080);
  form and form spans (ADR 0083); lead sheets (ADR 0084); timed lyrics
  (ADR 0085, which never copy words); notes and melody (ADR 0086);
  contributions and band membership ([ADR 0078](0078-contributions.md));
  the selection kind and title, and links of a take to a Work
  ([ADR 0048](0048-review-app-write-contracts.md)); and the sharing consent
  below. The list is in `world/circle.py`; any other contract is refused
  `operator_only`;
- pins only readable revisions, and supersedes only the caller's own records
  (or the band's lead sheet, below). A draft pinning anything else is
  refused `not_disclosed`.

**Onward sharing and disclosure changes stay the operator's.** A member
cannot send a transition: `/door/transition` and its dry run answer the
operator only, so publication approvals, withdrawals, retirement,
destruction, and upgrades stay the operator's. Nor can a member assert
`archive.claim/sensitivity` or `archive.claim/rights` (ADR 0085), or a work
origin (below). A limit a bandmate wants on content that records them is
still theirs under [ADR 0018](0018-people-in-the-archive.md): the operator
records it with the bandmate as its author, as today.

**A band's lead sheet is asserted by its members.** ADR 0084's adopted lead
sheet names the band's organisation as its `agent` and pins, in `adopted_by`,
the band memberships of the members who adopted it. A draft naming the
circle's band as its agent, rather than the caller, is accepted from a caller
only when it is an `archive.claim/lead_sheet` and one of its `adopted_by`
memberships names the caller as member. Any other draft naming another agent
is still `agent_mismatch`.

**The relay says who is calling and what it may do.** ADR 0060's `archive`
block gains `writes` (`dry-run` or `commit`, from configuration) and
`caller`, an object of `agent` and `standing` (`operator` or `member`). Two
refusal codes are added under ADR 0058's rule: `operator_only` (403: this
route, shape, or contract is the operator's) and `not_disclosed` (403: a
read or a draft names something the caller's circle cannot read). Logs still
keep the agent, never the login.

**Clients commit to a scratch relay, and to a live one after cutover.** This
amends ADR 0060's "clients refuse to send a commit unless `role` is
`scratch`". A client sends a commit only when the relay's `archive` block
says either:

- `role` `scratch`; or
- `role` `live` and `writes` `commit`.

Never to `shadow`, which stays `dry-run` (ADR 0056), and never to a relay
whose answer names no role or no `writes`. The relay's `writes`
setting stays the second, independent guard. A live relay is switched to
`commit` only by the cutover step that needs its own approval (condition 7);
nothing in a client stands in for that approval, and the client's rule only
stops a client from writing where the operator has not.

This reconciles the cutover plan's conditions 6 and 7 with ADR 0060:

- **Condition 6** is met on a scratch relay
  ([ADR 0061](0061-scratch-archives.md)), which takes its callers and
  `circle` from the source's configuration. Specification 0.0.10 is
  exercised there with commits by the operator and by members, before any
  other step. A scratch copy is the shadow's data, so a member's login on it
  is a disclosure to them like any other, under the same readable set.
- **Condition 7** reads "callers bound to the operator's person and to each
  circle member's person", and stays its own approval, after 0.0.10 has been
  exercised on scratch (D12.1), with the backups of condition 9.

**A Work's origin is a claim, and a co-written Work names its writers.**
`archive.claim/work_origin` version 1 answers ADR 0010's question for a
musical Work. Its payload is `{origin}`, one of:

- `own`: the operator wrote it alone;
- `co_written`: the operator wrote it with others;
- `others`: the operator is not among its writers.

Its references are exactly one `work`, a `musical_work` Entity revision; 0 to
32 `writer` person Entity revisions, distinct; and at most one `band`, an
organisation Entity revision, the band it was written in. A `co_written`
origin names at least two writers, the operator among them, and may name its
band. An `own` origin names no writer, or only the operator, and no band. An
`others` origin may name writers, never the operator, and no band. Its
`agent` is a person.

```json
{
  "agent": "urn:uuid:…(the operator's person)",
  "payload": {"origin": "co_written"},
  "refs": [
    {"role": "work", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "writer", "id": "urn:uuid:…(the operator's person)", "revision_id": "sha256:…"},
    {"role": "writer", "id": "urn:uuid:…(a bandmate's person)", "revision_id": "sha256:…"},
    {"role": "band", "id": "urn:uuid:…(the band)", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

Origins coexist like any claims. The ones that govern sharing are the head
origins asserted by the operator, or by a person who names themselves as a
writer in it. A Work's **writers** are the union of the writers those name,
and it is co-written when any of them says `co_written`. So the operator
cannot drop a co-writer by revising their own origin while the co-writer's
stands, and a stranger's origin adds no one. Writers are also what
[ADR 0078](0078-contributions.md)'s `work` contributions credit, but the two
are not derived from each other: a booklet may credit a writer whose consent
is not the archive's to ask, and an origin is the governance fact.

**A co-writer's consent is their own claim, recorded with the origin.**
`archive.claim/sharing_consent` version 1 is a writer's answer for one
destination. It pins exactly one `origin`, a `work_origin` version 1 Claim
revision whose origin is `co_written`, and 1 to 64 `covers`, the exact Claim
revisions the consent is for, as a publication approval's are. Its payload
has `destination` (1 to 200 characters, as the approval names it),
`decision` (`approve` or `refuse`), and optional `reason` (1 to 2,000
characters). Its `agent` must be a writer the pinned origin names, and never
the operator, whose consent is the publication approval itself.

```json
{
  "agent": "urn:uuid:…(a bandmate's person)",
  "payload": {"destination": "public site", "decision": "approve"},
  "refs": [
    {"role": "origin", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "covers", "id": "urn:uuid:…(a lead sheet)", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

A consent is a claim, not a transition: it authorizes nothing by itself, as
ADR 0018's limit is a claim; it is one condition of the operator's approval.
A member who is a writer asserts it through the relay; for a writer with no
login the operator records it with the writer as its author (ADR 0018). A
writer changes their answer by superseding their own consent; the head
revision speaks.

**The sharing gate.** Before a disclosure of covered revisions to a
destination, World computes the Works they touch: every `musical_work`
Entity in the transitive closure of their references, and every Work that a
`performance_work` claim links to an occurrence, a selection, or an Artifact
in that closure. For each co-written Work touched:

- **inside the band**, the operator's approval suffices: the destination's
  class is `private` or `circle`, and every person it reaches is a writer of
  the Work or a member of a band an origin names, by a band membership the
  operator asserted with no `to` date and not `ended`;
- **beyond the band**, any other destination, including a circle with someone
  outside the band, also needs, from every writer but the operator, a head
  `approve` consent naming that destination exactly, covering every
  disclosed revision, and pinning an origin of that Work.

A refusal, a missing consent, or a consent for another destination or
another revision blocks the share; the gate names each missing writer and
revision and says why. It never widens what sensitivity and rights allow
(ADR 0085), and it changes nothing for `own` or `others` Works, which ADR
0010 already covers. The circle's readable set applies the
gate with the circle's members as the people reached, so a circle that
includes someone outside the band reads a co-written Work only with every
co-writer's consent.

**Door's part.** Door admits a member's proposal as any other: the relay's
checks run first, Door's own checks are unchanged, and a supersession must
keep the agent of what it supersedes (D-3's supersession rule, now checked
for these contracts). A specification release installs `work_origin` and
`sharing_consent` with the Door profile rules they need: role counts that
depend on `origin`, an `agent` among the pinned origin's `writer`
references and not the active operator, and the pinned origin's own
`origin` value.

**The rules live in World until a release installs them.**
`world/circle.py` holds the configuration check, the member's draft check,
the readable set, the origin and consent rules, the writers union, the Works
a disclosure touches, the sharing gate, and the client's commit rule, as
World and the relay will apply them. It uses only the standard library, so
a client can check a proposal before the relay does.

**New nouns.** Circle (of a relay), member, standing, readable set, withheld,
work origin, writer, sharing consent, and sharing gate enter the
[vocabulary](../vocabulary.md). None is a subsystem: the circle is a
destination, a member is a caller, and the rest name a claim or a computed
view of claims. They own no verb beyond Annotation's *assert* and
Publication's *disclose*, and hold no state outside configuration and the
claims Door admits.

## Consequences

- A bandmate opens the application on their own device, sees the lead sheets
  and recordings the operator disclosed to the circle, and corrects a chord,
  marks a bar, or affirms a reading in their own name. The operator's view
  and theirs stand side by side.
- A co-written song is shared inside the band with one approval, and beyond
  it only with each co-writer's recorded yes, which outlives the session in
  which it was given.
- The operator's notes stay private until disclosed, even on a disclosed
  recording.
- Members cannot run SPARQL. A screen a member needs has to be a named view,
  which is ADR 0082's work.
- A circle member's claims are evidence from a person who is not the
  operator. Their governance stops at their own consent; ADR 0018's limit on
  what a single-operator archive can promise still applies: a consent the
  operator records for someone without a login is only as honest as the
  operator.
- Every readable-set refresh walks reference closures; at the scale of one
  band's songs this is small.

## Relation to other decisions

- [ADR 0010](0010-publication.md) and
  [ADR 0019](0019-publication-approval-is-a-transition.md): circle
  disclosure is a publication approval to a circle destination; origin gains
  a contract for Works; co-written Works get their own rule.
- [ADR 0016](0016-disclosure.md): a member's read is a disclosure, recorded
  in advance by the approval.
- [ADR 0018](0018-people-in-the-archive.md): consent, like a limit, is the
  person's own claim.
- [ADR 0056](0056-tailnet-relay.md): amended. Logins other than the
  operator's are allowed, as circle members.
- [ADR 0060](0060-relay-interface-and-role.md): amended. Clients commit to
  `scratch`, or to `live` in `commit`; the answer names `writes` and the
  caller; two refusal codes are added.
- ADR 0081: a member's judgments and
  marks are admitted under their own person, with no more and no less than
  the operator's.
- ADR 0082: members read only named views; the
  current rule's chosen person may be a member.
- ADR 0084: showing a lead sheet discloses its inputs;
  a band's lead sheet is asserted by its members.
- ADR 0085: sensitivity and rights cap the
  readable set and the gate; the document upload route stays the operator's.
- ADR 0089: practice audio is served to
  members only for readable recordings; a shared export passes the gate.

## Alternatives considered

- **A member's claims wait in a queue for the operator.** D10.1 admits them
  directly; a queue would make the operator the author of every bandmate's
  disagreement. Where machine proposals queue stays open.
- **Raw SPARQL filtered by the readable set.** Filtering arbitrary queries
  over a graph is a research problem; named views are a closed list the
  relay can check.
- **A member's own records private to them by default.** A bandmate shares
  in the band's space by asserting there; a private note belongs in their own
  tools, outside the operator's archive.
- **Members' claims disclosed only by the operator's approval.** Every
  bandmate correction would need an operator act before another bandmate
  saw it.
- **A new disclosure contract for the circle.** A publication approval to a
  circle destination already records who authorized what, for whom, pinned
  to exact revisions.
- **Co-writers' consent as a publication approval.** An approval's
  `authorizer` must be the active operator (D-13); a co-writer has no
  governance authority, only a say over their own work.
- **Origin from contributions.** A writer credit can come from a booklet or a
  database; whose consent is needed must be a fact a person asserted, not
  whatever a source printed.
- **The role decides commits by URL.** Rejected by ADR 0060 and still wrong.
