# 0096. Recordings and Artifacts have an origin

Status: proposed (wave 1, drafted 2026-10-19; open questions settled 2026-10-19)

[ADR 0010](0010-publication.md) says the `origin` claim decides whether
something is the operator's own work, which publishes once approved, or
anything else, including unknown origin, which needs an approval given for
that publication before it leaves the circle (INV-20, S-30). It names origin
values in passing (own capture, field recording, commercial release) and
keeps origin and rights as separate claims. No contract records origin for
content. [ADR 0088](0088-bandmates.md) gave a musical Work its
`work_origin`, but a recording, a sample, or a rendered file has none, and
the [open questions](../open-questions.md#the-archive-contract) still ask what marks the
operator's own work across media.

Three things now need it:

- **The matcher.** [ADR 0071](0071-essentia-cover-similarity.md) carries
  over the V2 system's thresholds but judges every corpus entry at the
  strict 0.08, because "the archive does not yet record whether corpus audio
  is the operator's own recording or a commercial release". The V2 system
  matched against commercial releases at a looser 0.11.
- **Friends' material.** The operator will hold friends' libraries in the
  archive, custody first. A drive of bought albums a friend hands over is not
  the friend's work and not the operator's; the archive has no way to say
  either thing, or whose drive it was.
- **The sample app and lineage.** A cut from a commercial album, a hit from
  a sample pack, and a phrase a synthesiser rendered are all "others' work"
  to ADR 0010 but differ in what follows from them. Lineage ([ADR 0098](0098-content-lineage.md)) caps
  an output by its sources and falls back to origin where there is no
  lineage, so origin must exist for lineage to bottom out.

The operator decided the following on 2026-10-19 (wave 1 decisions W1.10);
this record states it as given.

- **W1.10 Origin of recordings and Artifacts.** "A closed set:
  `own_capture`, `participant_capture` (naming the person),
  `commercial_release`, `sample_pack` (naming the publisher),
  `broadcast_or_web`, `generated` (synths, models), `unknown`. Origin names a
  person or organisation where it applies; a friend's deposited library
  carries the friend in a depositor role on the origin claim, not a new noun.
  This answers the open question on own versus commercial corpus audio."
- **Context (W1.13, ADR 0098).** "An Artifact with neither lineage nor
  origin stays unknown origin (others' work beyond the circle, today's
  default)."

The operator settled this record's open questions on 2026-10-19 (round 4
answers R4.4, R4.8 and R4.10; R4.11 settles the rest as recommended). The
decision below states the settled choices; the list at the end records them.

## Decision

### The contract

**`archive.claim/origin` version 1** says where content came from. Its
payload:

```json
{"origin": "sample_pack", "note": "kick folder of the pack", "url": "https://packs.example/drums-2", "retrieved_at": "2026-10-19"}
```

- `origin` (required) is one of the seven values below. The specification
  release ships them as a SKOS scheme, each concept with a definition;
  `commercial_release` is close to the PREMIS and Dublin Core sense of a
  published source, and no adopted vocabulary has the whole set, so the
  scheme is Archive's own ([ADR 0012](0012-inherited-meaning.md)).
- `note` (optional, 1 to 2,000 characters): free text, such as a catalogue
  number or the name of a broadcast.
- `url` and `retrieved_at` (optional, together, never with `own_capture` or
  `participant_capture`): where it was obtained and when, as in ADR 0078.
- `generator` (optional, 1 to 200 characters, only with `generated`): the
  instrument, patch, or model, by name and version.

Its references:

| Role | Count | Pins |
|---|---|---|
| `subject` | 1 to 1,000, distinct objects | an Artifact, or an Entity of kind `recording` ([ADR 0078](0078-contributions.md)) |
| `captured_by` | 0 to 8, distinct | person Entities: who held the recorder |
| `publisher` | 0 to 8, distinct | person or organisation Entities: a label, an artist, a pack's publisher, a broadcaster, a site, the maker of a synth or model |
| `release` | 0 or 1 | an Entity of kind `release` ([ADR 0087](0087-tracklists-and-correspondence.md)) |
| `item` | 0 or 1 | a physical item the subject was digitised from ([ADR 0013](0013-physical-collections.md)) |
| `run` | 0 or 1 | a derivation run claim that made it ([ADR 0064](0064-derivation-runs.md)) |
| `depositor` | 0 or 1 | a person Entity: whose library it came from |
| `document` | 0 or 1 | an Artifact: a receipt, a liner scan, a download page |

The roles allowed depend on the value:

| `origin` | Means | Must name | May name |
|---|---|---|---|
| `own_capture` | the operator recorded it, on their own device or session | nothing | `captured_by` the operator only, `document` |
| `participant_capture` | someone else recorded it | 1+ `captured_by`, at least one not the operator | `depositor`, `document` |
| `commercial_release` | a published recording, bought, ripped, or downloaded | nothing | `publisher`, `release`, `item`, `depositor`, `document` |
| `sample_pack` | a file from a sample or loop collection | 1+ `publisher` | `release`, `depositor`, `document` |
| `broadcast_or_web` | captured from radio, television, a stream, or a web page | nothing | `publisher`, `depositor`, `document` |
| `generated` | produced by a synthesiser, a model, or a program rather than recorded | nothing | `publisher`, `run`, `depositor`, `document` |
| `unknown` | someone looked and could not tell | nothing | `depositor`, `document` |

A `depositor` is never the operator, and never appears on `own_capture`.
"Participant" in `participant_capture` means the recorder, not a session
participant ([ADR 0095](0095-participation.md)): a friend's own recording of
a session the operator did not attend is `participant_capture` naming the
friend in `captured_by`, and the scheme's definition says so (R4.11).
`generated` is for what a program or model produced, and is others' work
even when it came from the operator's own instrument; a synth part the
operator played and recorded is `own_capture` (R4.11).
`unknown` is a statement ("I asked and nobody knows"); no claim at all means
nobody has said. Both are treated the same below.

```json
{
  "agent": "urn:uuid:…(the operator's person)",
  "payload": {"origin": "commercial_release", "note": "CD rip, 2009 pressing"},
  "refs": [
    {"role": "subject", "id": "urn:uuid:…(a FLAC Artifact)", "revision_id": "sha256:…"},
    {"role": "publisher", "id": "urn:uuid:…(the label)", "revision_id": "sha256:…"},
    {"role": "release", "id": "urn:uuid:…(the album)", "revision_id": "sha256:…"},
    {"role": "depositor", "id": "urn:uuid:…(a friend, Rosa Linden)", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

**Who may assert it.** The envelope `agent` is a person (an
`x-archive-agent` rule, as for `work_origin`). Door's profile rules:

- `own_capture` only from the active operator. It is the one value that
  loosens governance (below), so nobody else can claim the operator's work
  for them (R4.4).
- every other value from any person, because each only narrows: it makes
  content others' work, which needs more approval, never less. Through the
  relay, a member needs the `annotate` ability to assert origin about
  content they can already read, and `admit` to send it with content they
  admit ([ADR 0094](0094-peoples-writes.md)) (R4.4).
- a supersession keeps the agent of what it supersedes (D-3, as ADR 0088
  checks it).

A machine never asserts origin. The capture import proposes `own_capture`
only for content from capture grammars the operator has marked in
configuration as the operator's own devices, under the operator's person
with the importer as `by`; content from any other grammar gets no origin
claim from the import (R4.10).

**A member's admission carries an origin.** Every Artifact a member admits
through the relay comes with an origin draft in the same proposal; `unknown`
is allowed, so a member's content never arrives without anyone having been
asked (R4.11).

**Origin and `work_origin` are separate.** ADR 0010 separates a composition
from a recording of it, and so does this: a take of a co-written song that
the operator recorded is `own_capture` content of a `co_written` Work.
ADR 0088's sharing gate still applies through the Work; origin adds nothing
to it and removes nothing from it.

**Origin and rights are separate.** A sample pack is typically "derive yes,
redistribute no", but that is a rights claim by use ([ADR 0097](0097-rights-by-use.md)), not origin.
Origin never stands in for a rights claim: an `own_capture` recording still
needs a permitting rights claim to go beyond service (INV-21).

### Which origin governs

Origins coexist like any claims (INV-7). World computes an object's
**governing origins**: the head origin claims whose `subject` names it,
except one the operator has judged `rejected` ([ADR 0081](0081-judgments-and-annotations.md)),
mirroring how W1.12 lets only the operator's judgment set aside a narrowing
claim. From them:

- **Own work** is content with at least one governing `own_capture` and no
  governing origin of any other value. A bandmate who says they held the
  recorder makes it `participant_capture` content, others' work, until the
  operator rejects that claim. Disagreement lands on the protective side.
- **Others' work** is everything else, including content with no origin at
  all. INV-18 holds: a missing origin never withdraws or drops anything; it
  only withholds the looser treatment own work gets.
- **Content without its own origin**: a stem
  ([ADR 0075](0075-stems-as-content.md)) or derived media
  ([ADR 0069](0069-derived-media.md)) takes it from its source, since each is
  the recording it came from. A lineage output (ADR 0098) generally does
  not: an output with lineage and no origin claim of its own is unknown
  origin. The one exception (R4.11, ADR 0098): when a head `complete: true`
  lineage claim about the output was asserted or affirmed by the active
  operator, and not rejected, the output takes the most restrictive origin
  of its sources. It is own work only when every input is own work by these
  rules, and its governing origins are then its inputs' governing origins,
  shown as inherited. A member's lineage, or an incomplete one, never
  supplies origin, since that could widen. With neither origin nor lineage
  content is unknown origin, as W1.13 says.

### Reach

Origin is **not** an input to reach. Reach stays the strictest sensitivity
and rights over the closure (`Reacher` in `world/documents.py`,
[ADR 0085](0085-documents-lyrics-rights.md)), and lineage (ADR 0098) adds
its sources to that closure. Origin governs a different question, whose
approval is needed, which S-30 already words: work that is not the
operator's own needs "an approval given for that publication". Today every
publication approval is a person's act for one destination and exact
revisions, so S-30 is already met and Door's refusal of approvals beyond the
circle changes nothing in this release. Origin starts to bite when a
standing approval exists: a class-scoped approval, or one materialised by a
kind-based default ([ADR 0019](0019-publication-approval-is-a-transition.md)),
may cover own work only. `beyond_circle` and `may_publish` then name each
covered object that is not own work, with its governing origins as the
reason. The `why` lines of reach gain the governing origins for display, so
an application can say "a commercial release, deposited by Rosa" beside
"no rights claim: no further than service".

### The matcher's corpus

This answers the corpus question of ADR 0071. A corpus occurrence is
**release audio** when every Artifact it plays has governing origins that
are all `commercial_release` and at least one of them is the operator's.
A member's claim cannot loosen the operator's matching; any disagreement
returns the entry to the strict side.

- On the exact path, release audio may be suggested at the V2 system's 0.11;
  every other entry, own, participant, sample pack, generated, or unknown,
  stays at 0.08. The quarter-rate distance 0.118 has no release counterpart
  and stays for all.
- The run records `release_suggestion_distance` in its parameters and pins
  the origin revisions it read (ADR 0067), so a later change of origin is a
  reason to rerun, never a silent change to an earlier run's suggestions.
- 0.11 is not switched on yet. Origin is recorded now, and every entry
  stays at 0.08 until ADR 0071's check against the shadow's accepted
  performances includes release audio; then a later run switches it on
  (R4.11).

### Depositor and origin agent

Three people can stand around one recording, and they differ:

- the **claim's agent** is who says where it came from: the operator, a
  member, or a friend with a login;
- the **origin's named party** (`captured_by`, `publisher`) is who made or
  published it, whose work it is;
- the **depositor** is whose library the archive took it from: custody, not
  authorship. A friend who hands over a drive of bought albums is the
  depositor of `commercial_release` content; the label is its publisher.

The depositor role gives no authorship and no say over sharing: it is not
a writer (ADR 0088), not a captured-by party, and not a rights holder
unless a rights claim names them. It records provenance, so the archive can
answer "which of this came from Rosa's drive" years later, and a later
retire or destroy of a friend's deposit can be planned by query. A
`captured_by` person's consent does not gate sharing beyond the circle in
wave 1; their sensitivity limit already binds where the content records them
(W1.7), and a consent would need `sharing_consent` version 2 pinning an
origin claim (R4.11).

**A depositor's limits bind like a recorded person's (R4.8).** A depositor
may set a sensitivity limit on what they deposited, and it binds until they
lift it, exactly as the limit of a person the content records. A friend's
bought albums do not record them, so this needs INV-26 and ADR 0018 to say
so. When this record is accepted, they are amended as follows (neither is
edited by this proposal). [ADR 0094](0094-peoples-writes.md) applies the
rule at the relay: its records-them test admits a member's limit on content
whose head origin claim names them as depositor and which the operator has
not rejected.

*Proposed INV-26* (in `docs/invariants.md`; the changed words are "or
deposited"):

> **INV-26. A limit binds until its author lifts it.** The strictest
> sensitivity claim on an object caps its disclosure, and a limit set by a
> person the content records, or a person who deposited it, is lifted only
> by that person. The operator may go past it only for one named
> destination, no further out than service, with a recorded reason.
> (ADR 0018, ADR 0096; in `docs/invariants.md` both are links)

*Proposed amendment to ADR 0018*: its first rule's heading becomes "**A
person the content records, or who deposited it, may limit its
disclosure.**", and this paragraph is added after it:

> A depositor is the person whose library the archive took the content
> from, named in the `depositor` role of a head origin claim (ADR 0096)
> that the operator has not rejected. Custody of someone else's material is
> held on their behalf, so they keep a say over where it goes even when it
> does not record them. Their limit is a sensitivity claim whose source is
> that person, it binds exactly as a recorded person's does, and only they
> lift it. Being a depositor gives no authorship and no consent over
> sharing; it gives only the limit. If the operator later rejects the
> origin claim, the depositor can set no new limit through it, and a limit
> already set still binds until its author lifts it.

The rest of ADR 0018 (only the person withdraws; the operator's
one-destination override; nothing past a limit to open or distribution)
applies to depositors unchanged.

### World and the relay

- **Projection.** The next projection version (ADR 0059) adds
  `arch:origin`, `arch:captured_by`, `arch:publisher`, `arch:depositor`,
  `arch:origin_release`, `arch:origin_item`, and `arch:generator`, with
  their shapes.
- **Views.** A named view `origin` (ADR 0082) answers, for an Artifact,
  recording, or selection: own work or others', the governing origins, the
  ones the operator rejected, and the source it inherited from. `may_publish`
  gains origin blockers once standing approvals exist; the circle's readable
  set and `may_show` do not change.
- **Relay.** `origin` version 1 joins the member contracts of
  `world/circle.py`, over readable subjects as ADR 0088 requires, under the
  grants of [ADR 0094](0094-peoples-writes.md): `annotate` for readable
  subjects, `admit` with staged content (R4.4); `own_capture` from a member
  is refused `operator_only`. A member's admission (ADR 0094) carries an
  origin draft in the same proposal, `unknown` allowed (R4.11).

## Consequences

- The open question on own versus commercial corpus audio is answered: the
  archive records it, and the matcher reads it.
- ADR 0010's "the `origin` claim decides" has a claim to read for content,
  as `work_origin` gave one for Works.
- A friend's library is held with its depositor recorded, and nothing in it
  is ever mistaken for the operator's work.
- Most existing content has no origin and stays others' work. Nothing
  changes for it until someone asserts one; the operator's own rehearsal
  captures need `own_capture` claims, best proposed by the capture import.
- A sample-app render made from a sample pack is never own work: with no
  origin claim of its own it is unknown origin, and its lineage (ADR 0098)
  caps its reach by the pack's sensitivity and rights, however many renders
  later.
- The vocabulary's existing **Origin** entry gains a contract and a closed
  set; **depositor** is added as a role.
- A friend who deposits a library keeps a limit over it, though it does not
  record them, once INV-26 and ADR 0018 are amended (R4.8).
- The operator's rehearsal captures from their own configured devices get
  `own_capture` from the capture import; everything else needs a person to
  say (R4.10).

## Alternatives considered

- **Origin as an input to reach.** Reach answers how far content may go,
  from sensitivity and rights; origin answers whose approval is needed.
  Folding them would make an `own_capture` claim widen exposure without a
  rights claim, against INV-21.
- **Extending `work_origin` to content.** A composition and a recording have
  separate origins and rights (ADR 0010); a cover the operator recorded is
  `own_capture` of an `others` Work.
- **A depositor noun or a deposit transition.** W1.10 makes it a role; the
  admission and its provenance already record when content arrived.
- **Strictest origin by a ranking of values.** The values are not ordered
  beyond own versus others; only that distinction governs, and any
  disagreement lands on others.
- **Letting anyone assert `own_capture`.** It is the one value that loosens
  governance; a bandmate cannot make something the operator's.
- **Free-text origin.** W1.10 fixes a closed set; text belongs in `note`.

## New nouns

**Origin (contract).** The word exists in the vocabulary; this gives it a
contract and a closed set.

1. It says where content came from and whose it is, which nothing else does:
   rights say what may be done, `work_origin` covers compositions, and
   contributions credit roles without governing.
2. It owns no verb beyond Annotation's *assert*.
3. It enforces none of its own; it is what INV-20 and S-30 read to tell own
   work from others'.
4. It is a claim inside Annotation, read by World's views; no new code home.
5. No: the claims Door admits are the state; governing origins are computed.
6. A detail: one contract and one view.

**Depositor.**

1. It records whose library content came from, apart from who made it.
2. None; it is a role on an origin claim.
3. None.
4. It is a reference role, not code.
5. No.
6. A detail of the origin contract, as W1.10 says.

## Touch points

- `spec/contracts/` and the next `spec/0.0.x` catalog: the contract, the
  SKOS scheme of values, and the Door profile rules (role counts by value,
  `own_capture` from the active operator only).
- `door/src/Archive/Door/AgentRules.hs`: the agent rules, beside
  `sharing_consent`'s.
- `world/envelope_rules.py` `AGENT_KINDS`, and `world/contract_graphs.py`'s
  registry and `WORDS`.
- `world/projection/6/vocabulary.ttl` and `revision.ttl`, in their
  successor version.
- `world/documents.py` `Reacher`: an origin index beside sensitivity and
  rights, governing origins and own work, and origin lines in `why`.
- `world/circle.py` `MEMBER_CONTRACTS`, `check_member_draft`,
  `beyond_circle`, and `may_publish`; `world/record_views.py` `VIEWS` for the
  `origin` view.
- `enrich/enrich.py` `match_corpus` and the limit chosen near the exact
  path, and `enrich/cover.py` `SEARCH` for `release_suggestion_distance`.
- `capture/capture.py` `plan`: propose `own_capture` for operator-marked
  capture grammars (R4.10), and the capture configuration that marks them.
- `docs/invariants.md` (INV-26) and ADR 0018, amended as above on
  acceptance.

## Settled by the operator (2026-10-19)

1. **Switch on 0.11 now?** No: record origin now, keep 0.08 until ADR 0071's
   check includes release audio (R4.11).
2. **May a depositor limit what they deposited?** Yes; their limits bind
   like a recorded person's, with the INV-26 and ADR 0018 amendment text
   above (R4.8). ADR 0094 applies it at the relay.
3. **Should the capture import propose `own_capture`?** Only for capture
   grammars the operator configured as their own devices (R4.10).
4. **Must a member's admission carry an origin, and which ability covers
   origin?** Yes, in the same proposal, `unknown` allowed (R4.11); origin
   about existing readable content needs `annotate`, and `own_capture` stays
   operator-only (R4.4).
5. **A friend's own recordings of sessions the operator did not attend.**
   `participant_capture` naming the friend; "participant" means the recorder
   (R4.11).
6. **`captured_by` consent beyond the circle.** Not in wave 1 (R4.11).
7. **`generated` by the operator's own instrument.** Others' work; what the
   operator played and recorded is `own_capture` (R4.11).

### Still open

1. **Naming oneself depositor of existing content.** With `annotate` (R4.4)
   a member may assert an origin claim naming themselves as depositor of any
   readable content, and R4.8 then lets them limit it until the operator
   rejects the claim. *Recommend:* for limits, count a depositor only when
   the operator asserted or affirmed the origin claim, or when it came in
   the same admission as the content the depositor admitted.
