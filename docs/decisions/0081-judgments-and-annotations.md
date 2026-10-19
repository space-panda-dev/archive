# 0081. Judgments can scope to a span or reading; people can annotate spans

Status: proposed

A person can judge a claim only as a whole. `archive.assessment/judgment`
version 1 ([ADR 0048](0048-review-app-write-contracts.md)) pins one exact
Claim revision in `assessment_of` and says `affirmed`, `rejected`, or
`withdrawn` about all of it. A machine's chord stacks claim
([ADR 0077](0077-chord-stacks-and-meter.md)) covers a whole recording in
hundreds of spans, each with up to 16 candidate readings. A person who
agrees with all of it except ten seconds can either affirm every chord in it
or reject every chord in it. They cannot say "this span is wrong", "of the
two readings here, the second is right", or "here I hold the chart's chord
and not the machine's". A corrective claim can say what the chord is instead
(ADR 0077's `corrects`), but a person who only wants to agree or disagree
should not have to restate a chord.

A person also has nowhere to mark a place as worth coming back to. "I'm not
sure what the keys play here." "The guitar's chord differs from the mix's
here: listen." "Bandmate doubles the bass in this bar." None of these is a
fact about the music that a claim contract holds, and none is a judgment of
a claim. The app that uses these (learning songs as lead sheets, showing and
correcting machine output, and sharing with bandmates) needs both.

The operator decided the following on 2026-10-18; this record states it as
given.

- **D3.1.** "Judgment v2: affirmed/rejected/withdrawn, optionally scoped to
  one span or one reading of a claim. A full correction remains a corrective
  claim."
- **D3.2.** "Flags: person-asserted ANNOTATION claims on a span (kinds:
  unsure, interesting, note). Machine-detected differences (mix vs part, tool
  disagreement) are COMPUTED views served by World, not stored." A
  free-text note is allowed only for kind `note`, and it is the asserter's
  own text.
- **Implied.** "Cross-claim weighting: none (ADR 0005, INV-8). 'Prefer A
  over B' = judgments (affirm A's span, reject B's)." And: the mix's harmony
  differing from one part's local chord is not a disagreement but a place
  worth zooming into, and it must be expressible and findable.

## Decision

**`archive.assessment/judgment` version 2 is version 1 with an optional
scope.** Its shape is `assessment`, as version 1's is. Its references are
version 1's: exactly one `assessment_of`, pinning one exact Claim revision
of any contract. Its payload has version 1's `verdict` (`affirmed`,
`rejected`, or `withdrawn`) and optional `reason` (1 to 2,000 characters),
and at most one of three scopes:

- `span`, with `sample_rate`: an object of `start_sample` and `end_sample`,
  integers with `0 <= start_sample < end_sample`. The judgment is about what
  the claim says over those samples, and nothing else. The judged claim must
  be **timed**: its payload states an integer `sample_rate` and it pins
  exactly one `stream`. The judgment's `sample_rate` equals the judged
  claim's, and the span ends at or before that stream's `sample_count`.
  Chord stacks, meter layers, a `span` contribution
  ([ADR 0078](0078-contributions.md)), span suggestions
  ([ADR 0066](0066-span-suggestions.md)), and a span annotation (below) are
  timed. The rule reads the claim's shape, not a list of contracts, so a
  timed contract added later is judged by span with no new judgment version.
- `entries`: an object of `first` and `last`, integers with
  `0 <= first <= last`, a range of the judged claim's `entries`, which it
  must have and whose length `last` is below. A chord sequence (ADR 0077) is
  judged this way.
- `reading`: an object of `reading`, the index of one reading, and exactly
  one of `span` or `entry`, the index of the span or entry it belongs to,
  each an integer of at least 0. The judged claim must have that span (in its
  payload's `spans`; the claim must then be timed) or entry (in its
  `entries`), and that span or entry must have that reading in its
  `readings`.

```json
{
  "payload": {
    "verdict": "rejected",
    "reason": "The bass moves to the fourth here.",
    "sample_rate": 48000,
    "span": {"start_sample": 2880000, "end_sample": 3360000}
  },
  "refs": [
    {"role": "assessment_of", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

```json
{
  "payload": {"verdict": "affirmed", "reading": {"span": 41, "reading": 1}},
  "refs": [
    {"role": "assessment_of", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

A version 2 judgment with no scope judges the whole claim, exactly as
version 1 does. Indices are exact because the reference pins one revision:
a revision never changes, so span 41 of it is always the same span. A later
revision of the claim is a different revision and is not judged by this
one.

**Version 1 is unchanged and stays admissible.** Every version 1 judgment
keeps its meaning: a judgment of the whole claim revision. Version 1 does not
gain a scope; a scoped judgment cites version 2. Version 1's bundle stays
installed with its exact bytes.

**What a scoped verdict means.** A judgment of a span or a range of entries
judges what the claim says there, every reading as the claim ordered them.
A judgment of one reading judges that candidate alone and says nothing of
the span's other readings: affirming the second reading of a span does not
reject the first, and a person who holds the second and not the first makes
two judgments. No judgment implies anything outside its scope. `affirmed`
says the judge holds the claim there, `rejected` that the judge holds it
false there, and `withdrawn` that the asserter takes that part of their own
claim back. Nothing judged is rewritten (ADR 0005, INV-7).

**A withdrawal is the asserter's own.** A version 2 `withdrawn` judgment's
envelope `agent` must be the judged claim's `agent`: only the asserter can
take a claim back, in whole or in part. Anyone else who thinks it false
rejects it. Version 1 never checked this, and keeps not checking it.

**A correction is still a claim.** Judging a span or reading never says what
the chord is instead. A person who hears another chord asserts it, in a chord
stacks claim that cites what it `corrects` (ADR 0077). A judgment can choose
only among the claim's own readings, which is what D3.1's "one reading" is
for: a machine offered two candidates and the person heard the second.

**"Prefer A over B" is two judgments.** There is no weighting across claims
and no ranking of asserters (ADR 0005, INV-8). A person who hears, over some
seconds, a chart's chord and not a machine's affirms the chart claim over
those samples and rejects the machine claim over the same samples. Each
judgment is about one claim, can be revised alone, and stands as that
person's view. Another person's view is their own judgments. ADR 0082's
first current rule reads them as "the chosen person's corrections and
affirmations".

**One judge's standing.** World computes, never stores, what one judge holds
of one claim revision at one position (a sample of a timed claim, or an
entry of a sequence), for one reading or for the claim's statement there as
a whole. It reads only that judge's judgments of that exact revision, and
only head revisions: a judgment its own judge superseded no longer speaks.
A judgment of the whole claim applies everywhere; of a span or range, at
every position in it, to every reading; of one reading, only to that reading
of that span or entry. The narrowest applicable judgment stands: a reading
over a span or range, a span or range over the whole claim, and a span or
range inside another of the same kind over that one. So "I affirm this
claim, except here" is two judgments, and both stay true. Where the
narrowest applicable judgments disagree (two overlapping spans, neither
inside the other), the standing is a conflict and has no verdict
(INV-24). The standing names the judgments that decided it and says why. It
never combines judges: a view that sets two people's judgments side by side
reads each person's standing and shows both. The standing is not a current
rule; a current rule may read it.

**`archive.claim/span_annotation` version 1: a person's mark on a span.** One
claim is one mark, so a person can withdraw, and another can affirm or
reject, exactly one mark. Its asserter is a person: its envelope `agent` is
a person Entity, never a Package URL, and it pins no `run` and no
`document`. It marks one of two things:

- **samples of a recording**: it pins exactly one `source` Artifact revision
  and exactly one `stream`, an `archive.claim/audio_stream` version 1 Claim
  revision about that same Artifact revision, and its payload has
  `sample_rate` (equal to the stream's) and `span` (`start_sample` and
  `end_sample`, with `0 <= start_sample < end_sample <= sample_count`);
- **entries of a chord sequence**: it pins exactly one `sequence`, an
  `archive.claim/chord_sequence` version 1 or 2 Claim revision (version 2,
  ADR 0079, keeps version 1's entries), and at most one
  `work`, which must be the Work Entity revision that sequence pins; its
  payload has `entries` (`first` and `last`, with
  `0 <= first <= last` and `last` below the sequence's entry count). This is
  how a mark sits on a Work: on the written order of chords a sequence gives
  for it.

It pins never both and always one. Its payload's `kind` is one of:

- `unsure`: the person is not sure of what is here;
- `interesting`: the person marks this place as worth looking at again;
- `note`: the person's own remark, in `text`.

`text` (1 to 2,000 characters) is present exactly when the kind is `note`.
It is the asserter's own words. It never quotes a lyric or a document: words
of a song stay in their document, under its own sensitivity, as ADR 0077
keeps them out of a chord sequence.

A mark may pin up to eight `about` Claim revisions, distinct, and never its
own stream or sequence: the claims the person was looking at. Each `about`
claim must pin the mark's subject under some role: for a mark on samples,
the same `source` Artifact revision; for a mark on entries, the sequence's
Work or document revision. A mark cites what it is about but judges none of
it: an `interesting` mark about two claims that differ disputes neither.

```json
{
  "payload": {
    "kind": "interesting",
    "sample_rate": 48000,
    "span": {"start_sample": 5760000, "end_sample": 6240000}
  },
  "refs": [
    {"role": "source", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "stream", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "about", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "about", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

```json
{
  "payload": {
    "kind": "note",
    "text": "The second time through, the turnaround skips the IV.",
    "entries": {"first": 12, "last": 15}
  },
  "refs": [
    {"role": "sequence", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "work", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

**A mark changes as claims do.** Its asserter takes it back with a
`withdrawn` judgment (whole, or over part of its span) or revises it by
superseding it; an `unsure` mark that has been resolved is withdrawn, not
edited. Another person agrees with a mark by affirming it, or makes their
own. A mark is a timed claim when it marks samples, so a judgment may scope
to part of its span.

**Differences are computed, never marked by a machine.** A mix's chord and
one part's chord differing over the same seconds (keys playing a 6/9 voicing
while the guitar plays a major seventh), or two tools disagreeing, are not
conflicts: two claims about different things (the whole sound, and one
player) or by different asserters, each standing. Finding them is World's
computed **difference** view (ADR 0082), which is regenerable and never
admitted. A machine never asserts a span annotation. When a person wants to keep one of those places, they mark it
`interesting`, `about` the claims that differ; the person's mark is evidence
of their attention, and the difference itself stays computed.

**Who may judge and mark.** Any person may judge any claim and mark any span,
in their own name. ADR 0088 decides that a bandmate's judgments and marks
are admitted under their own person agent; this record
gives them nothing more and nothing less than the operator's. A judgment or
a mark is about its subject and is shown with it, so it is never disclosed
further than what it judges or marks; the sensitivity of a note's own text
is ADR 0085's.

**The rules live in World until a release installs them.**
`world/judgments.py` holds every rule above as World would re-check it, and
the standing view. It uses only the standard library, so a client can check
a proposal before Door does. Door cannot admit either contract until a
specification release installs their bundles. The release adds the Door
profile rules this record needs:

- a judgment scope checked against the judged revision: the judged claim is
  timed (an integer `sample_rate` and one `stream`), the scope's
  `sample_rate` equals it, the span ends within that stream's
  `sample_count`, and a named entry, span, or reading exists in the judged
  payload;
- `sample_rate` present exactly when `span` is, and at most one scope;
- a `withdrawn` judgment's `agent` equal to the judged claim's `agent`;
- a span annotation's `agent` a person Entity, its exactly-one-of subject
  (`source` with `stream`, or `sequence` with optional `work`), the existing
  same-subject and target-measure rules for its stream and span, the
  existing target-index rule for its entries, a `work` equal to the
  sequence's, `text` exactly for `note`, distinct `about` references other
  than its frame, and each `about` claim pinning the mark's subject.

**New nouns.** Scoped judgment, standing, and span annotation enter the
[vocabulary](../vocabulary.md). A span annotation is one kind of claim that
Annotation (the concept) asserts; the concept's meaning does not change, and
the contract is not called "annotation" so that the two are never confused.
None is a subsystem: a scoped judgment is an assessment's payload, a span
annotation a claim's, and standing a view of assessments. They own no verb
beyond Review's *assess* and Annotation's *assert*, enforce no new
invariant, and hold no state outside the records Door admits.

**For the operator to confirm.** The decisions above go beyond D3.1 and D3.2
in these places, each chosen here and open until the operator accepts this
record: a `withdrawn` judgment must be the asserter's own (version 2 only); a
range of a sequence's `entries` is a scope beside a span of samples, so a
Work's chords can be judged and marked in part; the narrowest judgment
stands in one judge's standing; a mark may cite the claims it is `about`;
one mark per claim; a judgment or mark is disclosed no further than its
subject; and the contract's name, `span_annotation`.

## Consequences

- A person can agree with a machine's chords in general and reject ten
  seconds of them, or pick the second of its two readings at one span,
  without restating a chord. A full correction still takes a corrective
  claim.
- "Prefer A over B" exists only as one person's two judgments, so a view can
  always say whose preference it shows and why.
- One person's earlier and later judgments of the same claim combine by
  scope: a narrower judgment stands where it applies. Disagreeing judgments
  of the same width by the same person are reported, not resolved.
- A bandmate and the operator may each judge the same span differently; each
  standing is computed alone, and neither overwrites the other.
- Places worth returning to are claims, so they survive, are attributed, and
  can be listed per recording or per Work. Machine-found differences are
  regenerated, never accumulated.
- Parts of claims with no position axis here (a Work's untimed meter or form,
  a Work-level melody, a key region over a Work's sections) can be judged
  only whole until a later version adds their axis (ADRs 0080, 0083, and
  0086).
- No invariant changes. INV-7 keeps every claim and judgment, INV-8 ranks no
  asserter, and INV-24 makes the standing say when it cannot choose.

## Rejected

- **Scoping version 1.** A version 1 judgment would gain a second reading
  (was an old judgment about all of it?), and a reader of version 1 would
  have to learn new fields. Version 2 is additive.
- **A list of spans per judgment.** Each span would be revised and withdrawn
  together. One scope per judgment keeps each one separately revisable; a
  gesture that judges many spans proposes many judgments in one batch.
- **Identifying a reading by copying it.** Matching a copied reading against
  the claim is fragile (two equal readings, a reordered list) and repeats the
  claim. An index into a pinned revision is exact.
- **An adjudication record pinning A and B.** One assessment that ranks two
  claims states a preference that is neither claim's truth, and could not be
  revised for one claim alone. Two judgments say the same and keep each
  claim's judgment its own.
- **A weight or preference field across claims.** INV-8: a weight compares
  readings within its own claim only.
- **Marks as a judgment verdict** (`unsure` beside `affirmed`). "Interesting"
  is about the music, not a claim, and may cite no claim at all.
- **Storing machine-found differences as marks.** D3.2: they are regenerable,
  and storing them would grow without bound and go stale when a claim is
  added.
- **Free text on every kind.** D3.2 keeps text to `note`; `unsure` and
  `interesting` stay comparable and countable.
- **Many marks per claim** (a list of spans, as span suggestions hold). A
  person's marks are tens per recording, not thousands, and one mark per
  claim lets each be withdrawn or affirmed alone.
- **Naming the contract `annotation`.** Annotation is a concept in the
  vocabulary (a person asserting any claim) and a false friend of Web
  Annotation, which World already maps claims onto.
