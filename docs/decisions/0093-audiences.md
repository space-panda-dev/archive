# 0093. A relay has several named audiences

Status: accepted (operator, 2026-10-19); proposed in wave 1 and its open
questions settled the same day

[ADR 0088](0088-bandmates.md) gives a relay one circle: a destination of
class circle, named in the relay's configuration, with a list of members and
an optional band. Everything the operator discloses to members goes to that
one list. That fits one band. It does not fit the people the operator now
wants to read the archive: a band, a crew of friends who produce, a duo with
someone outside the band. Each should read only what was disclosed to them,
and a co-written song shared with the band should not reach the producers
without its co-writers' consent.

The circle also lives where the archive cannot see it. Who is in it is a
configuration edit. Adding a person discloses to them everything already
approved to the circle, but the archive holds no record that they were added,
or when. ADR 0088 calls the approval the record of disclosure. With one list
that was close enough. With several lists, and with grants that name the
lists ([ADR 0094](0094-peoples-writes.md), proposed), the archive must hold the lists itself.

Three applications will read World through the relay: the rehearsal review
app, a lead-sheet songbook, and a sample app. Friends and bandmates will log
in to all three ([ADR 0092](0092-internet-exposure.md), proposed). Each login
reads through the audiences its person belongs to.

The operator decided the following on 2026-10-19 (wave 1 decisions W1.8 and
W1.15); this record states it as given.

- **W1.8 Audiences.** "A relay has several named audiences (for example a
  band and a producers' crew), each a member list; a publication approval to
  the circle names its audience. A small named group of people the operator
  knows is circle class (private); anything self-signup is service or open.
  The readable set is computed per audience."
- **W1.15 Federation stays open**, recorded in the
  [open questions](../open-questions.md).

The operator settled this record's open questions on 2026-10-19 (round 4
answers R4.2 and R4.9; R4.11 settles the rest as recommended; round 5
answer R5.1 settles the last). The decision below states the settled
choices; the list at the end records them.

## Decision

**An audience is a destination of class circle whose members the archive
records.** It is a small group of people the operator named one by one. Its
name is the destination name a publication approval gives, so approving to
an audience needs no new approval contract: the approval's `destination` is
the audience's name. A group anyone can join by signing up is not an
audience. It is a destination of class service or open (W1.8), reached only
by a version 2 approval of that class, under the check beyond the circle
([ADR 0085](0085-documents-lyrics-rights.md), amended 2026-10-19).

**Who is in an audience is an operator transition.** Membership decides what
a person can read, so changing it is a governance act, recorded before
anything is served, in the same way that W1.5 makes grants transitions.
`archive.transition/audience` version 1 declares one audience. Each revision
states the whole membership.

```json
{
  "agent": "urn:uuid:…(the operator's person)",
  "payload": {"name": "Harbor Light crew"},
  "refs": [
    {"role": "authorizer", "id": "urn:uuid:…(the operator's person)", "revision_id": "sha256:…"},
    {"role": "member", "id": "urn:uuid:…(a friend who produces)", "revision_id": "sha256:…"},
    {"role": "member", "id": "urn:uuid:…(a bandmate)", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

- `name` (1 to 200 characters) is the destination name, exactly as an
  approval names it.
- `ended` (optional, `true`) closes the audience. An ended revision names no
  members.
- `authorizer`: exactly one. It pins the current revision of the active
  operator's person, as ADR 0051 requires of an approval.
- `member`: 1 to 64 person Entity revisions, all distinct. The operator's
  person may be listed (R4.2). That changes only what the operator's
  internet login reads, which is member capacity
  ([ADR 0092](0092-internet-exposure.md)); over the tailnet the operator
  reads everything already (ADR 0088). None when `ended`.
- `band`: at most one organisation Entity revision. It takes over ADR 0088's
  configured `band`: the band whose members may assert its lead sheet, and
  whose own records count as members' records in this audience.

**Door's rules.** `door transition` dispatches the contract under ADR 0028's
manifest, lock, expected-heads, and single-commit rules. `door admit` refuses
it. Door refuses a revision when:

- its authorizer is not the active operator;
- a `member` is not an Entity of kind `person`;
- the `band` is not an organisation;
- it supersedes anything other than the current head of the same audience
  object;
- it changes the `name` of the audience it supersedes. Approvals name
  audiences by name, so a renamed audience would leave its approvals pointing
  at nothing, or at the next audience to take the name;
- it uses a `name` that any other audience object has used, live or ended. A
  name is never reused, so an old approval never reaches a new group;
- it supersedes an `ended` revision. An ended audience stays ended. A new
  group is a new audience with a new name.

Door also refuses a `publication_approval` version 2 whose class is `circle`
and whose destination is not the name of a live audience. Today that mistake
silently discloses nothing. With audiences in the archive, Door can say so.
A version 1 approval stays unchecked, as ADR 0085's amendment keeps it.

**The readable set is computed per audience.** World computes ADR 0088's
readable set once for each live audience, with that audience's name as the
destination and its members as the people reached:

1. **Covered**: what live approvals to the audience's name cover, when the
   sharing gate lets it reach that audience;
2. **What it pins**, transitively, as before;
3. **Members' own**: claims and assessments whose agent is a member of *this*
   audience other than the operator's person, or its band, when everything
   they pin is readable in this audience. Listing the operator in an
   audience never discloses the operator's records: they still need an
   approval;
4. **The audience itself**: its head revision and its members' person
   Entities, so a member knows who else reads what they write.

Sensitivity applies to every audience as it does to the circle. Rights never
limit an audience, because each audience is circle class, which is private
(ADR 0085). The operator's own records still need an approval, and withheld
records are still counted (INV-24).

A member reads the union of the sets of the audiences they belong to, plus
their own records. The operator's internet login reads the union of the
sets of the audiences that list the operator (R4.2), plus the operator's
own records whose pins are readable in one of those audiences, as any member
sees their own; those records join no audience's set, so other members still
see them only once approved (R5.1). A record a member asserts reaches the audiences they
belong to in which what it pins is readable, and no others. A producer's note
on a song disclosed to both the band and the crew reaches the crew. It
reaches the band only when the producer is also in the band, or when the
operator approves that note to the band. Onward sharing stays the operator's
(ADR 0088).

**The sharing gate runs per audience.** ADR 0088's "inside the band" test
already takes the people a circle destination reaches. That set is now the
audience's members. A co-written song approved to the band passes on the
operator's approval. The same song approved to the producers' crew, which
includes someone outside the band, is blocked from that audience's readable
set until every co-writer has given a head `approve` consent naming the
crew's audience name and covering the revisions. The operator's person,
when listed, is not counted among the people the gate checks: reaching the
operator discloses nothing. Nothing else in the gate changes.

**What the relay does.** The relay's `callers` configuration still maps a
tailnet login to a person (ADR 0056), now only the operator's (ADR 0092),
and ADR 0092's `internet.logins` maps each verified Access email to one. The
relay reads the live audiences from the selected World generation, and they
change with each snapshot:

- A caller is in `member` capacity when their person is in at least one live
  audience, or when they are the operator on the internet ingress (ADR
  0092). A caller who is neither the operator on the tailnet nor a member of
  any live audience is refused `forbidden_identity`; that includes the
  operator's internet login while no audience lists the operator. ADR 0088's start-up rule that every member
  is a caller is dropped: a person may be in an audience before they have a
  login, and an approval to that audience may overcount them, which is
  allowed (ADR 0016).
- The answer's `caller` object gains `audiences`: the names of the caller's
  live audiences, sorted. The operator's tailnet answer lists none; the
  operator's internet answer lists the audiences that name the operator.
- Named views, media routes, and member drafts check against the caller's
  readable set (the union above), exactly where ADR 0088 checks against the
  circle's. A draft that names an audience's band as its agent is checked
  against the bands of the caller's own audiences.
- The relay's configured `circle` goes away: membership lives in the
  archive (R4.9). Until the specification release that installs the audience
  contract, the relay keeps reading ADR 0088's configuration as one
  audience; that release is the switchover (R4.9). To migrate, the operator commits one audience transition with the
  old destination name and members. Every approval already given names that
  destination, so it keeps its effect, with nothing re-approved. After that,
  a relay configured with a `circle` while World holds a live audience
  refuses to start, so membership has one source.

**World's views.** `archive.view/readable_set` version 2 takes as its subject
an audience object, read from World, instead of a configured circle. Its
`reader` parameter must be the operator or a member of that audience.
Version 1 stays for a relay still on configuration. A new operator-only
view, `archive.view/audiences`, lists each audience's head revision: name,
members, band, whether it is ended, and the live approvals to it. Its
`person` parameter gives the audiences a person belongs to. `may_publish` is
unchanged: an approval to an audience is circle class, and `may_publish`
answers no for it, as it does today.

**The rules live in World first.** As with ADR 0088, `world/circle.py` would
hold the audience check, the per-audience readable set, and the union, using
only the standard library, so a client can check a draft before the relay
does.

## Consequences

- The operator can disclose a mix to the band and a rough cut to the
  producers' crew, and each reads only theirs. Someone in both reads both.
- Adding a person to an audience is a committed transition. It records
  before anything is served that they can now read what that audience was
  approved. Removing them is another transition. What they already read was
  disclosed and stays recorded as disclosed.
- Grants (ADR 0094, proposed) name audiences by name. Because a name is
  never changed or reused, a name in a grant always means one audience
  object, and that audience is in the archive.
- A co-written song needs co-writers' consent for each audience that reaches
  beyond its band. A band-only audience needs none.
- Sensitivity cannot tell one audience from another. `AMBER` lets an object
  reach every audience. Keeping something from the crew but not the band is
  done by not approving it to the crew; there is no per-audience label
  (R4.11). An `AMBER` object still reaches no member who reads through the
  internet, because Cloudflare is a service processor; the "circle" setting
  will write `GREEN` instead (ADR 0092, R4.1).
- The operator can see what an audience sees from any browser by listing
  themselves in it. Over the tailnet, `archive.view/readable_set` still
  previews any audience.
- A personal-use licence, such as a bought tablature, reaches every audience
  as it reached the one circle, because every audience is private; the
  64-member cap keeps audiences small (R4.11).
- A destination anyone can sign up to is not served through the relay until
  a publisher exists that calls `may_publish` (ADR 0085) (R4.11).
- An audience never names another archive. Someone else's content enters
  only by the operator holding it in custody (W1.15).
- The readable set is computed once per audience for each snapshot. At a few
  audiences of a few dozen people this is small.

## Alternatives considered

- **Several circles in relay configuration.** This extends ADR 0088 with the
  least change. But membership would stay invisible to the archive, so
  adding a person would disclose with no record, and grants in World could
  not name an audience.
- **An audience as an Entity, with membership as claims.** Claims coexist and
  anyone may assert one. Membership decides disclosure, which is governance
  authority (INV-20, INV-25), not evidence for a view to weigh.
- **One membership transition per person.** The current membership would
  then be a fold over adds and removes. A revision that states the whole list
  can be checked by reading it.
- **A new `audience` field on the publication approval.** The destination
  already names where a disclosure goes. An audience *is* that destination,
  and a second field could disagree with it.
- **Self-signup audiences.** These are excluded by W1.8. A group the operator
  does not know member by member is not circle class.
- **Rights limiting a large audience.** ADR 0085 decided that the circle is
  private. W1.8 makes every audience circle class. Drawing a line by size
  would be a new decision; the operator kept ADR 0085's rule (R4.11).
- **Keep the operator out of every audience**, previewing through
  `archive.view/readable_set`. R4.2 chose the opposite, so the operator's
  internet login has something to read.

## New nouns

**Audience** (of a relay) replaces ADR 0088's "circle (of a relay)" as the
noun. "The circle" stays the name of the exposure class. Member, capacity,
readable set, and withheld keep their meanings, per audience.

1. *What does it do that nothing else does?* It records which people a
   circle-class destination reaches. Today nothing in the archive records
   that.
2. *Which verb does it own?* None of its own. It is declared and changed
   through *transition* (Door). Publication's *disclose* is what reaches it.
3. *Which invariant does it enforce?* INV-25: every disclosure is recorded,
   now including who it reached. INV-20 holds per audience, because an
   approval names one audience.
4. *Why can it not be code inside an existing concept?* It is not code. It
   is a destination, as ADR 0010 defines one, whose reach is recorded as
   evidence. Its rules sit inside `world/circle.py` and Door's transition
   dispatch.
5. *Does it own authoritative state?* Only its transition claims, which Door
   admits. The readable set is a view and is never stored.
6. *Is it really a subsystem?* No. It is a recorded destination plus a
   projection (the readable set).

## Touch points

A later implementation would change:

- `world/circle.py`: `Circle` (becomes an audience: name, members, band);
  `check_circle_config` and `capacity` (membership from World, a caller in no
  audience refused); `check_member_draft` (the band from the caller's
  audiences); `_approvals_to` and `readable_set` (one audience's name and
  members, rule 4 added); `readable_by` (the union of the caller's
  audiences); `sharing_gate`, which is unchanged but is now called per
  audience; a new check for `archive.transition/audience`.
- `world/record_views.py`: `_circle_subject` and `readable_set` (version 2,
  with an audience subject); a new `audiences` view.
- `relay/access.py`: `check_config` (the `circle` configuration is
  retired), `Access.__init__` and `Access._roster` (one readable set per
  audience; member persons checked from World), `Access.readable`,
  `Access.check_drafts`, `load`, and `Caller` (gains `audiences`).
- `door/src/Archive/Door/TransitionDryRun.hs`: `transitionDispatchTable`,
  `dispatch`, `planTransition` (the new kind and its rules), and
  `validatePublicationReach` (a circle-class version 2 approval must name a
  live audience).
- `door/src/Archive/Door/SharingGate.hs`, `Reach.hs`, and `BeyondCircle.hs`:
  no change. They govern beyond the circle, and an audience is inside it.
- A specification release: the `audience` contract and the transition
  dispatch entry. Also `docs/vocabulary.md` (the circle entry) and ADR 0088,
  which this record amends (membership in the archive; the operator may
  be listed, for the internet ingress only).

## Settled by the operator (2026-10-19)

1. **Where membership lives.** In the archive, as an operator transition,
   amending ADR 0088's configuration (R4.9).
2. **Personal-use licences in a larger audience.** Kept as ADR 0085 decided;
   every audience is private, and the 64-member cap keeps them small (R4.11).
3. **A limit for one audience.** No per-audience label; the operator does not
   approve to that audience, and a member's own limit can narrow to private
   (R4.11).
4. **Members seeing each other.** Yes, by rule 4; a person Entity labelled
   `RED` stays withheld (R4.11).
5. **The member cap.** 64 (R4.11).
6. **Relay service for a self-signup destination.** Unserved until a
   publisher exists that calls `may_publish` (R4.11).
7. **The operator in an audience.** The operator may be listed; the
   operator's internet login reads the union of the audiences that list it
   (R4.2). Listing never discloses the operator's records (rule 3).
8. **When the relay stops reading configuration.** At the specification
   release that installs `archive.transition/audience`; a relay with both a
   configured `circle` and a live audience refuses to start (R4.9).
9. **The operator's own unapproved records over the internet.** The
   operator's internet login also sees the operator's own records whose pins
   are readable in one of its audiences, as any member sees their own,
   without adding them to any audience's set (R5.1).
