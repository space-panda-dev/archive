# 0097. Rights say which use they permit, and a clearance permits one use

Status: proposed (wave 1, drafted 2026-10-19)

A rights claim ([ADR 0085](0085-documents-lyrics-rights.md)) gives its
subject one furthest class. The permit table turns the RightsStatements.org
statement into that class (`InC` held by the operator and `NoC-US`
distribution, `NoC-NC` open, `personal-use-licence` the circle, anything else
service), and reach applies it to the subject and to everything that pins
it. One class cannot tell three things apart that a licence routinely
separates:

- **listening**: letting an audience hear or see the subject;
- **using it in a derivative**: making something new from it, a song built
  on a sample, a remix, a bounce with a borrowed loop;
- **redistributing it as is**: handing out copies of the subject itself.

A sample pack is the common case: its publisher keeps copyright (`InC`, not
held by the operator, so service today), yet the licence lets a buyer
release music built from the samples commercially, and forbids passing the
samples on. Under ADR 0085 a track built from such a sample can never go
beyond service, and nothing records that the pack may not be passed on even
where its statement would otherwise allow it. Three applications are about
to make derivatives routine: the review app cuts samples, the sample app
files them beside organised libraries, and content lineage
([ADR 0098](0098-content-lineage.md), proposed) caps an output's
reach by its sources. Lineage needs to know what a source permits *for
derivatives*, not for listening.

Permission for one particular use is also missing. A friend who produces
says yes to one of their loops in one of the operator's songs, for a
commercial release; a label clears one sample in one recording. Neither is a
rights statement about the source: the source stays `InC`, and the yes
covers only that song and that kind of destination. Today the
beyond-circle check (ADR 0085, amended 2026-10-19; `x-archive-beyond-circle`)
reads only rights statements, so such a yes cannot be recorded or honoured.

The operator decided the following on 2026-10-19 (wave 1 decisions W1.11);
this record states it as given.

- **W1.11 Rights by use, and clearance.** A rights claim can say what use it
  permits: listen, use in a derivative, redistribute as is (a sample pack is
  typically derive-yes, redistribute-no). A **clearance** claim records
  permission for one use, in one Work or recording, for one destination
  class; the beyond-circle check reads it. Wave 1, because reach depends on
  it.

## Decision

### Three uses

A **use** is one of `listen`, `derive`, or `redistribute`:

| Use | Means | Exercised by |
|---|---|---|
| `listen` | an audience may hear or see the subject, without a copy to keep | a named view, a streamed listening copy, practice audio played in a page |
| `derive` | the subject may be an input to a new Artifact that reaches the class | a lineage source (ADR 0098) of whatever is shared |
| `redistribute` | an audience may receive copies of the subject as is | a download, a published file, a sample pack passed on |

The specification ships the uses as a small SKOS scheme beside the rights
statements, each concept `skos:closeMatch` the nearest W3C ODRL action
(`odrl:play`, `odrl:derive`, `odrl:distribute`). Close, not exact: ODRL
actions are policy terms, and a use here only selects a column of reach.

### Rights version 2: a class per use

**`archive.claim/rights` version 2** is version 1 with one optional payload
field, `uses`, an object whose keys are uses and whose values are the
furthest class (`circle`, `service`, `open`, or `distribution`) the licence
lets that use reach:

```json
{
  "statement": "InC",
  "uses": {"derive": "distribution", "redistribute": "circle"},
  "text": "royalty-free in your own productions; samples may not be resold or shared",
  "document_url": "https://packs.example/licence",
  "retrieved_at": "2026-10-19"
}
```

with references as version 1: the pack as `subject`, the publisher as
`holder`, the operator as `licensee` only when the statement is
`personal-use-licence`, and the licence as `document`.

- **A use the claim does not name takes the statement's class**, by ADR
  0085's permit table, unchanged. So the sample pack above listens no
  further than service, as `InC` not held by the operator always has.
- **A use named beyond the statement's class needs the licence.** A `uses`
  entry wider than the permit table's class for the statement is admitted
  only when the claim pins a `document`: widening past the table is a fact
  about a licence, and the licence is its evidence. Narrowing needs nothing.
- **Terms bind every use.** Outside the claim's `from` and `until` on the day
  it is read, every use reaches service, as version 1 does.
- **The circle is still private.** `circle` is the narrowest value: rights
  never limit the circle (ADR 0085, S-30), per use or otherwise.
- **The statement stays RightsStatements.org.** `statement` keeps the same
  closed list and URIs, and `uses` never changes which statement a claim
  makes. RightsStatements.org says whether and how a work is in copyright and
  is deliberately not a licence; `uses` records what a licence beside it
  granted. A consumer that knows only RightsStatements.org reads the
  statement and loses nothing it could have read before.
- **A version 1 claim permits every use at the table's class**, exactly as
  today. Every answer reach gives over version 1 claims is unchanged.

**The strictest claim still wins, per use.** For each subject and use, the
class is the narrowest that any head rights claim about the subject gives
that use, whoever asserted it. A subject with no rights claim reaches
service for every use (INV-21). Rights claims stay the operator's to send
through the relay ([ADR 0088](0088-bandmates.md)); a stranger's claim can
only narrow.

### Clearance: one use, in one Work or recording, for one class

**`archive.claim/clearance` version 1** records that someone with the power
to grant it permitted one use of one subject, in one Work or recording, for
one destination class. Its references:

- exactly one `subject`: what is cleared, an Artifact or an Entity of kind
  `musical_work`, `recording`, or `release` (the subjects a rights claim can
  have);
- exactly one `in`: where the use happens, an Entity of kind `musical_work`
  or `recording`, or an Artifact (see open question 3);
- 1 to 8 `grantor` person or organisation Entities: who said yes;
- at most one `document`: the permission as received (a letter, a licence,
  an exported message).

Its payload:

```json
{
  "use": "derive",
  "class": "distribution",
  "from": "2026-10",
  "until": "2031",
  "jurisdiction": "GB",
  "text": "the loop may be used in the song for release on all platforms",
  "document_url": "https://mail.example/thread/1",
  "retrieved_at": "2026-10-19"
}
```

`use` and `class` (`service`, `open`, or `distribution`) are required; the
rest are optional, with ADR 0085's rules for partial dates, jurisdiction,
text, and document fields (`x-archive-partial-dates`). The envelope `agent`
is a person.

- **Only the operator's clearances count.** A clearance widens reach, so
  only head clearances whose `agent` is the active operator are read. A
  friend's yes is recorded by the operator with the friend as `grantor`; the
  friend's own login cannot assert a clearance, and the relay refuses one
  `operator_only` (ADR 0088's member contracts do not gain it).
- **A clearance is a claim, not a transition.** It authorizes no disclosure
  by itself, as a co-writer's consent does not (ADR 0088): it is one
  condition the beyond-circle check reads. The publication approval is
  still required (INV-20), still per destination and exact revision.
- **A clearance names one class and covers that class.** A clearance for
  `distribution` does not clear `open` (open question 4).
- **A clearance lifts rights only.** Never sensitivity, never a co-writer's
  consent (the sharing gate), never the fresh approval that others' work
  needs (INV-20).
- **A clearance needs a rights claim to lift.** It lifts a rights limit only
  on a subject that has a head rights claim; a subject with none stays at
  service, whatever clearance names it. So "a rights claim that permits
  them" (INV-21) is still read literally: the clearance permits the use, and
  the rights claim says whose the subject is.

### Reach by use

**Reach becomes a class per use.** World's reach (`world/documents.py`)
gives each object its sensitivity class, as today, and a rights class for
each of the three uses. `Reach.furthest(use)` is the narrower of the
sensitivity class and that use's rights class. Sensitivity has no use:
a person's limit holds for every use (ADR 0018).

**Which use reads which object.** Showing or sharing an object exercises one
use on it and on everything in its closure (ADR 0085: everything it pins,
transitively, through every revision): showing a lead sheet that pins a
recording listens to the recording too; downloading it redistributes both.
Pins keep the exercised use, never `derive`, because a pin is not a claim
that one thing was made from another (open question 5). Lineage is: for
every source an output's lineage reaches (ADR 0098 walks it backward), the
use read is `derive`, whatever the use exercised on the output.

**What the relay and applications exercise.** Inside the circle nothing
changes: rights never limit it, and every audience is circle class
([ADR 0093](0093-audiences.md)). For a destination beyond the circle
(which the relay does not serve yet; ADR 0093, its question 6), named views,
listening copies, peaks, and practice audio played in the page are
`listen`; a file the relay hands over to keep (an export, a document's
bytes, a rendered mix offered as a download) is `redistribute`.
`may_show` gains a `use` argument, `listen` by default.

### The beyond-circle check reads the use and the clearances

**A publication approval names its use.** Publication approval version 3
adds a required `use`, the use the publication exercises (a
website that streams a song is `listen`, a store that sells downloads is
`redistribute`). An approval of version 2, which names none, is checked
against all three uses, each of which must pass: the strictest reading, so
no existing approval reaches further than it does today. ADR 0093 adds no
approval version (an audience is the approval's `destination`), so
version 3 carries only `use` (open question 2).

**The check, per covered revision**, on the UTC date of the transition
(Door) or the publish date (`may_publish`), for the approval's use *u* and
class *c*:

1. sensitivity of the closure allows *c*, unchanged;
2. every rights subject in the closure permits *u* at *c*, by the strictest
   head rights claim about it, except a subject reached only through a
   lineage edge, which step 3 reads;
3. every rights subject among the lineage sources of the covered object
   (ADR 0098) permits `derive` at *c*;
4. a subject blocked in step 2 or 3 is lifted when a head clearance by the
   active operator names it as `subject`, has the use that step read
   (*u* or `derive`), has class *c*, is in force on the day, and has as
   `in` the covered object itself, an object in its closure, or a Work the
   covered revisions touch by ADR 0088's computation (`works_touched`);
   and the subject has a head rights claim;
5. ADR 0088's sharing gate passes, unchanged.

Door names each blocker as today, adding the use, and for a subject a
clearance could lift, that no clearance for that use, class, and Work or
recording was found. Door and World agree on the same snapshot and date, as
the amendment of ADR 0085 requires; the agreement test gains cases for each
use and for clearances.

### Contract changes

1. `archive.claim/rights` version 2: version 1 plus optional `uses`, with
   the rule that a widening entry pins a `document`.
2. `archive.claim/clearance` version 1, as above, with profile rules for its
   role counts and reference targets (`x-archive-reference-targets`).
3. `archive.transition/publication_approval` version 3: a required
   `use`; `x-archive-beyond-circle` reads it, and reads every use for
   version 2.
4. The uses SKOS scheme in the specification's vocabulary section.
5. Door: per-use rights limits and clearance lifting in the beyond-circle
   rule; neither contract needs a dispatch change (both are claims, admitted
   by `door admit`).
6. World: per-use reach; `may_show(use)`; `may_publish` and the `reach`
   view per use and with clearances; the projection of both claims and the
   uses scheme.

## Consequences

- A sample-pack loop can be built into a song that is released, while the
  pack itself stays inside the circle. A friend's loop can be cleared for one
  song and one class, and the clearance outlives the conversation in which
  it was given.
- Every answer over version 1 rights claims is unchanged; an archive that
  never writes version 2 or a clearance sees no difference.
- Lineage (ADR 0098) gets the column it needs: a source's `derive` class.
  Without this record, lineage would cap outputs by listen-or-share rights
  and block every licensed sample.
- A version 2 approval is checked against all three uses, so the archive's
  existing behaviour is the strictest case, never a looser one.
- The circle is untouched: rights, uses, and clearances matter only beyond
  it, so 0.0.10's readable set needs no change for circle audiences.
- Clearances carry evidence of a third party's word the operator recorded.
  Like ADR 0018's limits and ADR 0088's consents for people without a login,
  a clearance is only as honest as the operator who records it; the pinned
  document is what an auditor checks.
- Reach grows from one rights class to three; at single-archive scale the
  cost is the same walk read three ways.
- No invariant changes. INV-21 is applied per use, and a clearance never
  lifts a subject that has no rights claim.

## Alternatives considered

- **Creative Commons or ODRL as the rights statement.** ADR 0085 rejected
  Creative Commons for works no licence covers, and a full ODRL policy is a
  language Door would have to evaluate. Three uses, each a class, cover the
  cases in front of the archive and map onto ODRL actions if a later version
  needs them.
- **A use flag (yes or no) instead of a class per use.** "Derive yes" alone
  cannot say "for release" versus "for a demo page", and the permit table
  already speaks in classes.
- **Separate rights claims per use** (`statement` plus one `use`). The
  strictest-claim rule would then need to know which claims speak to which
  use, and a version 1 claim would have to be read as three. One claim with
  a column per use keeps "strictest per subject" a single rule.
- **Uses that may widen without a document.** A widening is the one rights
  fact that lets content further out; asking for its licence is cheap and
  S-32 asks that a fact which cannot be established never widen.
- **A clearance as a rights claim on the source.** A clearance covers one
  Work or recording; written as a rights statement it would widen the source
  for every song.
- **A clearance as a publication approval.** An approval's authorizer is the
  active operator (D-13); a clearance records a third party's permission,
  which the operator's approval then relies on.
- **A clearance from the grantor's own login.** It widens reach; letting a
  member's own claim widen would let anyone with a login clear anything they
  name themselves grantor of. The operator records it, naming them.
- **Reading pins as `derive`.** A lead sheet pins a recording because it was
  read from it, but a chord claim pins its stream as a locator too; treating
  every pin as derivation would widen a lead sheet whenever the recording
  allows derivatives, without anyone having claimed it was made from it.

## New nouns

**Use (of a right).** One of `listen`, `derive`, `redistribute`.

1. It separates what a licence permits for hearing, for making new work, and
   for passing copies on; nothing else does.
2. It owns no verb: reach reads it; Publication's *disclose* exercises it.
3. It enforces none of its own; it is how INV-21 and S-30 are applied per
   kind of use.
4. It could be, and is: a payload field and a column of reach, not a
   concept with code of its own.
5. No authoritative state beyond the rights claims and approvals that name
   it.
6. A detail of rights and reach.

**Clearance.** One person's or organisation's permission for one use of one
subject, in one Work or recording, for one destination class.

1. It records a permission scoped to one target, which a rights statement
   about the source cannot without widening it everywhere.
2. Annotation's *assert*; it owns no verb of its own.
3. It enforces none; the beyond-circle check (S-30) reads it, and INV-20 and
   INV-21 still hold around it.
4. It is code inside existing concepts: a claim contract, read by the reach
   and beyond-circle code in `world/documents.py`, `world/circle.py`, and
   Door's `Reach` module.
5. No state outside the claims Door admits.
6. A claim kind, not a subsystem.

## Touch points

- `world/documents.py`: `check_rights` (version 2's `uses` and the
  document rule), `_rights_limit` (a use argument), `Reach` (a class per
  use, `furthest(use)`), `Reacher._reach` (per-use rights, indexing head
  clearances by subject), `reach`, `may_show` (a `use` argument); a new
  `check_clearance`.
- `world/circle.py`: `beyond_circle` (the approval's use, lineage sources at
  `derive`, clearance lifting), `may_publish`, `works_touched` (reused for a
  clearance's `in`); `MEMBER_CONTRACTS` stays without rights and clearance.
- `world/record_views.py`: `reach`, `may_publish`, and `readable_set` views
  (a `use` parameter; a destination beyond the circle reads `listen`).
- `relay/access.py`: `Access._reach` (reach per use, once a destination
  beyond the circle is served; downloads read `redistribute`).
- `door/src/Archive/Door/Reach.hs`: `rightsLimit` and `reachBlockers` (a use
  argument, clearance lifting), `Index` (clearances by subject).
- `door/src/Archive/Door/BeyondCircle.hs`: `beyondCircleRule` (a `use`
  field name), `beyondCircleBlockers` (the approval's use, or all three for
  version 2).
- `door/test/BeyondCircle010.hs` and
  `world/tests/test_door_agreement_beyond_circle.py`: per-use and clearance
  cases, Door and World agreeing.
- `spec/archive-spec.md`: S-30's paragraph "Publication beyond the circle",
  the rights contract's section, the rights statements' SKOS block (the uses
  scheme beside it), and the contract catalog.
- `docs/vocabulary.md`: use and clearance, when accepted.

## Open for the operator

1. **May a `uses` entry widen past the permit table only with a pinned
   licence document?** Recommended: yes, as written; narrowing needs nothing.
2. **Which approval version carries `use`?** Version 3. ADR 0093 adds no
   approval version: an audience is named by the approval's existing
   `destination`, so there is no second version 3 to merge with.
   Recommended: accept version 3 for `use` alone.
3. **May a clearance's `in` be an Artifact, not only a Work or recording
   Entity?** The operator's own bounce often has no recording Entity.
   Recommended: yes, an Artifact or a `musical_work` or `recording` Entity.
4. **Does a clearance for one class also clear narrower classes?**
   Recommended: no, exactly the class it names, as W1.11 says "one
   destination class"; a second clearance is cheap, and permissions for a
   release do not always cover a free web page.
5. **Do pins carry the exercised use, and only lineage carry `derive`?**
   Recommended: yes for now; a later record may name which pins are
   derivations (a transcription's recording), which could widen lead sheets
   read from licensed material.
6. **Should a clearance require its `document`?** Recommended: no, as for
   rights version 1; a spoken yes recorded with the grantor named is still
   evidence, and the operator's approval relies on it knowingly.
7. **May a participant or member assert a rights claim that only narrows?**
   W1.7 lets members narrow sensitivity; rights stay the operator's (ADR
   0088). Recommended: not in wave 1; clearances stay operator-only always,
   because they widen.
8. **Does an origin of `sample_pack` ([ADR 0096](0096-origin-of-recordings.md)) imply a rights claim?**
   Recommended: no; origin and rights stay separate claims (ADR 0010). The
   sample-pack importer (wave 2) proposes a version 2 rights claim from the
   pack's licence for the operator to admit.
9. **Is a clearance's `jurisdiction` read?** Recommended: recorded, not
   computed, like rights' `jurisdiction`, until the open question on
   jurisdiction-specific terms is answered.
