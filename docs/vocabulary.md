# Vocabulary

These words have fixed meanings in this project. Use them exactly. Changing what
a word means is an architectural change and needs a
[decision record](decisions/README.md).

## The concepts

| Concept | Verb | Responsibility |
|---|---|---|
| Archive | preserve | Durable evidence |
| Door | admit / transition | Sole authority for archival meaning |
| World | relate / query | Assemble evidence into a broader referential universe |
| Enrichment | derive | Machine/computational derivation without source mutation |
| Annotation | assert | Human-authored claims |
| Review | assess / adjudicate | Judge claims and settle between competing ones |
| Policy | declare | Human-authorized desired archival state |
| Plan / apply | reconcile | Compute the difference from policy, then act on it |
| Spool | distribute | External domain-agnostic work protocol |

These are meanings, not services or programs. One program may do several of
them, and one of them may span several programs. What must not happen is one
quietly taking over another's job.

**Archive** keeps evidence for decades and owns what it knows.

**Door** is the only authority for archival meaning. To *admit* is to bring
something new into the archive; to *transition* is to change archival state
(record a claim or assessment, revise policy, publish, supersede, retire,
destroy, hand over to a new operator). Door checks validity, not desirability,
and it does not move bytes. ([ADR 0002](decisions/0002-one-door.md))

**World** relates the archive's evidence to the outside world and answers
questions across it. It is a projection: rebuildable, never the owner.
([ADR 0011](decisions/0011-world.md))

**Enrichment** derives new evidence from existing evidence without changing its
inputs: a transcript, a loudness measure, a format identification.

**Annotation** is a person asserting a claim: "this recording is Paper Lanterns".

**Review** judges claims. To *assess* is to judge one claim; to *adjudicate* is
to settle between competing claims. Review need not be human.

**Policy** is the operator's declaration of what the archive should hold,
written in a small rule language. It is different from **configuration**, which
tells a program how to run (`b2_bucket = "..."`).
([ADR 0007](decisions/0007-policy.md))

**Plan / apply**: *plan* works out what should change to meet policy, and why;
*apply* carries it out. ([ADR 0008](decisions/0008-plan-and-apply.md))

**Spool** is a separate project, in its own repository, that keeps durable work:
a task is put on a spool, leased by a worker, then acknowledged with a result
or failed. Archive uses it; it knows nothing about Archive. Spool's *reclaim*
takes back a stalled lease and is unrelated to a reclaim verdict.

## Three meanings of "archive"

- **Archive**, capitalised: the system and its contract.
- **an archive**: an actual collection of archival data on disk. It lives outside
  this repository.
- **this repository**: the source code and specification. It never holds
  personal archival data.

## Records

How evidence is stored. ([ADR 0003](decisions/0003-record-model.md))

**Record**: one immutable revision of something, in one of four shapes:

- **artifact**: an object whose bytes the archive keeps;
- **entity**: a durable subject the operator asserts, such as a song, a person,
  or a book on the shelf;
- **claim**: a typed, versioned statement about other things;
- **assessment**: a judgment of an exact claim revision.

**Object**: the enduring thing records describe, with a stable identifier.
**Content**: bytes, identified by their hash; several objects may share the same
content. **Location**: where content currently sits; moving it changes no
record. **Occurrence**: one arrival of content; the same WAV imported twice is
two occurrences of one content.

**Revision**: one recorded state of an object. **Supersede**: to replace a
revision explicitly. **Reference**: a typed link to another object. **Pin**: a
reference to an exact revision.

**`by`** and **`agent`**: every record names the program that wrote it (`by`)
and the person, model, or process that asserted it (`agent`).

**Contract**: the versioned definition of a claim kind's payload.
**Current**: the claim that currently counts for a question, computed from
claims and assessments by the contract's rule, never stored.

## Evidence and state

**Evidence**: anything whose loss would lose information.
**Regenerable state**: anything that can be rebuilt from evidence, however
expensively. **Operational state**: bookkeeping a program keeps to do its job,
such as routine verification results. See [state-model.md](state-model.md).

**Provenance**: where a claim or transition came from: who, how, from what, and
under which policy.

**Evidentiary authority**: how strongly a claim should be believed.
**Governance authority**: who may cause a consequence; the operator's.
([ADR 0005](decisions/0005-claims-review-and-authority.md))

**Operator**: the person with governance authority over an archive; one at a
time. **Succession**: the recorded handover to a new operator.

**Transition**: a change to archival state that Door records, as a claim of a
transition kind.
**Consequential transition**: one that someone may later need explained ("why
is this only on cold storage?", "why is this gone?").

**Promotion**: bringing a fact from World into the archive as a claim.

**Quiet history**: the rule that history is written only when something
meaningful happened.

**Feature**: a measurement of content that an enrichment tool makes for other
tools to read, such as a pitch-class profile every 93 ms or a separated stem;
named `<extractor>/<output>`. A feature is not a claim and is never admitted.
It is one of five kinds: a *series* (rows of numbers on a time grid), a
*scalar*, *events*, *spans* (time ranges), or a *file*.
**Feature bank**: the regenerable store of features, filed by content digest
and keyed by everything that decides them; tools *read* it. **Extractor**: a
program in any language, with a manifest, that *extracts* features into the
bank under the extractor contract. (Proposed:
[ADR 0073](decisions/0073-feature-bank.md),
[ADR 0074](decisions/0074-extractor-contract.md).)

## Deletion

([ADR 0004](decisions/0004-deletion.md))

**Retire**: take an object out of the collection; its records stay.
**Destroy**: remove content from the archive's custody and from every copy it
controls. **External survivor**: a known copy the archive cannot remove.
**Information erasure**: removing what derived evidence reveals about destroyed
content; not yet designed. **Expunge**: erase a record and its history; rare and
manual.

## Copies

([ADR 0009](decisions/0009-copies-and-custody.md))

**Failure domain**: places likely to fail together: one disk, one house, one
provider account.

**Managed copy**: a copy git-annex controls. **Observed copy**: an outside copy
the archive can verify but not control. **Projection**: the same bytes seen
again, such as a symlink. **Source**: where content came from before admission,
such as a recorder card.

**Reclaim verdict**: the archive's statement that a source is safe to clear.

**Staging host**: a machine that holds new content in its own git-annex
repository and proposes it to the Door host.

## Publication and disclosure

([ADR 0010](decisions/0010-publication.md),
[ADR 0016](decisions/0016-disclosure.md))

**Disclose**: let content leave the operator's custody readably. **Publish**: disclose so
that others can see. **Processor**: a destination that receives content to
compute on it and shows it to no one else, such as a hosted model.

**Destination**: somewhere content is published or sent. **Exposure class**:
how exposed a destination is: private, circle, service, open, or
distribution.
**Sensitivity**: a claim a person makes about an object, in Traffic Light
Protocol labels (RED, AMBER, GREEN, CLEAR). An application's three settings
are three of them: operator (RED), circle (AMBER), and published (CLEAR).
**Reach**: how far an object may be shown or shared, computed from the
sensitivity and rights of everything it pins; a view, never stored.
**Personal-use licence**: a rights statement that a subject was licensed to
one person for their own use, as a bought tablature is; it keeps the subject,
and what pins it, within the circle. No rights statement limits the circle,
which is private.
([ADR 0085](decisions/0085-documents-lyrics-rights.md))

**Circle (of a relay)**: the relay's destination of class circle, reached by
its **members**, each a bandmate's login bound to their own person. A
caller's **capacity** is `operator` or `member`. The **readable set** is
what a member may read: what an approval to the circle covers, what that
pins, and members' own records over readable things; a record kept from a
caller is **withheld**, and an answer counts what it withheld. **Work
origin**: a claim whether a Work is the operator's own, co-written, or
others', naming its **writers**. **Sharing consent**: a co-writer's own
claim approving or refusing one destination for exact revisions. The
**sharing gate** lets a co-written Work's content beyond the band only with
every co-writer's consent. ([ADR 0088](decisions/0088-bandmates.md))

**Origin**: where content came from (own capture, commercial release, ...).
**Rights**: what may legally be done with it, recorded with terms and dates.

## Collections and meaning

**Physical item**: a thing in the collection whose bytes the archive cannot
keep, such as a record, a book, or a notebook. It is admitted and retired like a
library holding. ([ADR 0013](decisions/0013-physical-collections.md))

**Context**: the file, copied into every archive, that maps record fields to
standard meanings. **Controlled vocabulary**: a list of allowed values kept as
data (a SKOS scheme). ([ADR 0012](decisions/0012-inherited-meaning.md))

**Preservation planning**: the work, done through policy, of keeping content in
formats that will stay readable.

**Usage observation**: a periodic count of how content was used (plays, views),
from an outside service.

## Chords and meter

([ADR 0077](decisions/0077-chord-stacks-and-meter.md))

**Chord stack**: what sounds at once, as a bass pitch class, the pitch classes
above it, and optionally a root, which need not sound. Not a chord name: a
stack has as many names as it has candidate roots.
**Reading**: one candidate an asserter gives for a span: a stack, "no chord",
or a source's text left uninterpreted. A span's readings are in the
asserter's order of preference and may carry weights on that claim's own
scale. **Uncertain tone**: a pitch class the asserter is unsure of: exactly
one of a group sounds, or one or none may. **Voicing**: a stack's actual
notes, with their octaves. **Computed name**: a Harte string or lead-sheet
symbol derived from a stack; a view, never the claim. The source's own symbol
is the reading's **text**, kept verbatim. **Held end**: a span end the
asserter did not hear or read but inferred, holding a chord until the next
change by a stated rule; weaker evidence than an asserted end. **Chord
sequence**: chords in the order a document or a person gives them, with no
times, about a Work or a document rather than a recording; each **sequence
entry** is located by line and word, never by the lyric's text. A
**correction** is a claim that names the claims it corrects; they stay, and
the resolved view shows the correction first where they overlap.

**Meter layer**: one counting of the music, as a sequence of bars or, in a
**beat-only layer**, as beats alone with no time signature. Several layers
over the same samples are polymeter or cross-rhythm, and none is primary. A
**felt layer** is a feel heard over a written one (a triplet feel in 4/4).
**Bar**: a span of samples with a numerator and denominator, and optionally
its **beat grouping** (2+2+3) and the samples of its counted beats. **Metric
modulation**: an explicit link between adjacent bars saying which note value
before lasts as long as which after, so that the tempo changes by their
ratio. **Regrouping**: a link between adjacent bars where every note value
keeps its length and the felt pulse moves (quarter to dotted quarter, the
eighth unchanged). **Tempo** is derived from bars and beats, never stored.

**Document**: an outside source's admitted bytes (a transcription file, a
saved chart) that a claim pins to say where its readings came from. A
**locator** is a position in the document's own numbering (its bar, its
beat). A claim's **document alignment** is how a document's positions were
placed on a recording's samples, and by which method; it is not a capture
alignment, which relates two recordings' clocks. **Mix**: the recording as heard, every
part together; machine claims are made from it first, and a claim made from a
separated part is a further claim beside it.

**Bank citation**: a stem, or any `file` output of the feature bank, cited in
a claim's payload by extractor, entry key, output, and file digest instead of
being admitted. **Bass-only reading**: a reading that says which pitch class
is lowest, and optionally the root, and nothing above it; a stack with empty
tones says instead that the bass alone sounds. **Derived from**: a claim's
lineage, the claim revisions its asserter read it from, with or without a
run; it replaces and ranks nothing. **Proposer**: an enrichment method that
reads bank entries and proposes claims from them.
([ADR 0079](decisions/0079-machine-harmony-claims.md))

**Key reading**: a **pitch collection** (the notes a passage draws on), with
an optional **tonic** and **mode**; or no key; or a source's text. A **key
sequence** holds keys over a Work's entries or sections, **key regions**
over a recording's samples. **Tuning**: where the twelve-tone **grid** lies
on a recording, as a reference offset in cents and **drift spans** where it
lies elsewhere; pitch classes name the nearest step of the grid in force.
**Spelling**: the letter names a key gives the twelve pitch classes; a view.
([ADR 0080](decisions/0080-key-and-tuning.md))

**Form**: a Work's or document's **sections** in written order, each with
its source's label and an optional **section kind**, with repeats, **endings**
(voltas), and **jumps** (D.C. and D.S., to a **segno**, a fine, or a
**coda**). Its **played order** is a view. **Form spans**: sections at a
recording's samples. **Untimed meter**: a meter layers claim of a Work's
bars, with no samples, tied to sections and sequence entries; a **placed
bar** is a timed bar that names the written bar it was placed from.
([ADR 0083](decisions/0083-form-and-work-meter.md))

**Notes claim**: one **part**'s notes on one recording (a stem or a
document's track), over its **range**, inside which silence is asserted;
an **unpitched part** keys drums by General MIDI. A **written length** is a
count of note values. A part's **instrument fields** are its open
**strings** and **capo**, as a document gives them. **Work-level melody**:
the composition's tune, untimed. **Melody spans**: which notes claim
carries a recording's tune over each span, a **selection**; a **hand-off**
is where the tune moves to another part.
([ADR 0086](decisions/0086-notes-and-melody.md))

## Review and views

**Scoped judgment**: a judgment of one span of samples, one range of entries,
or one reading of a claim. **Standing**: what one judge holds of one claim
revision at one place, where the narrowest applicable judgment stands; a
view. **Span annotation**: a person's mark on a span of a recording or a
range of a sequence's entries, of kind unsure, interesting, or note (the
operator's "flags"). ([ADR 0081](decisions/0081-judgments-and-annotations.md))

**Named view**: a question World answers by a stated, versioned rule, never
stored. A **current rule** is a named view that chooses, per question and
for a named person; its **tiers** rank evidence for that question only. A
**difference** is a computed place where a part and the mix, two parts, or
two tools hear differently: never a conflict.
([ADR 0082](decisions/0082-views-and-current.md))

## Lead sheets and practice

**Lead sheet**: a person's or a band's claim choosing the exact chord,
meter, form, key, lyric, and melody claim revisions that make their lead
sheet of a Work, optionally for one recording. An **adopted lead sheet** has
the band as its agent and pins the memberships of those who adopted it. A
lead sheet's **part** is one player's line, with optional instrument
fields. **Changes since**: later revisions, corrections, and judgments of
what a lead sheet pins; a view. ([ADR 0084](decisions/0084-lead-sheets.md))

**Timed lyrics**: a claim placing a lyric document's lines and words at a
recording's samples by line and word number, never copying a word, under a
**locator scheme** (plain text or LRC). **Document source**: a claim of what
kind of document an Artifact is and where, and when, it was retrieved.
([ADR 0085](decisions/0085-documents-lyrics-rights.md))

**Practice render**: a recording's stems mixed with chosen gains, optionally
pitch-shifted and time-stretched, as derived media, named by the **practice
key** of its **practice request**. **Transposition**: a view parameter that
moves what is shown by semitones; never a claim. **Export**: a regenerable
file (PDF, MusicXML, LilyPond, JAMS) rendered from exact revisions by a named
exporter, named by its **export key**; a **render** of a lead sheet is one.
A **loss** is what an export could not express. A sent export is admitted
as an Artifact. ([ADR 0089](decisions/0089-practice-audio-and-exports.md))

## Credits

([ADR 0078](decisions/0078-contributions.md))

**Contribution**: a claim that a **contributor** (a person or organisation
Entity) took part in a **role** in one **scope**. The role is a MusicBrainz
relationship type, with its attributes, instruments, and vocals; a contribution
may keep only the source's words when no type fits. Not a billing credit: an
`artist_credit` says whom a selection is presented as by, a contribution says
who did what.
**Scope**: how much of the music a contribution is about: a Work, a
**release** (one edition as issued), a **recording** (a recording, or a take
in the archive), a **span** (sample ranges of a recording), or a **channel**
(one source within a recording: a stem, an input channel's own file, or one
channel of a multichannel file). **Credit text**: the credit exactly as the
source printed it, kept beside the role.
A finer claim **refines** a coarser one when it names the same contributor and
role about part of the coarser subject; a claim with no contributor that
matches only **locates** it. A claim nothing refines is **finest**. Refining
never contradicts: no credit asserts absence.
**Band membership**: a claim that a person was a member of a band (an
organisation), with instruments and partial from and to dates.

**Tracklist**: a release's **media** and their **tracks**, in order, each
track a recording. **Recording audio**: a claim tying a recording Entity to
archived audio, whole or in part. **Catalogue correspondence**: a claim that
an artist, release, or recording is the same as, close to, or (for a
MusicBrainz release group or a Discogs master) narrower than an entry in an
outside catalogue. **Credits per track**: the view of which credits apply to
one recording, including those of its release.
([ADR 0087](decisions/0087-tracklists-and-correspondence.md))

## Naming discipline

When an established concept is meant, use its established word:

| Say | Not |
|---|---|
| admit | ingest, import, upload |
| Door | validator, gatekeeper, write service |
| World | knowledge base, facts store, metadata store, graph database |
| evidence, claim | facts, knowledge |
| Enrichment | pipeline, processing, analysis service |
| Policy | rules engine, config, workflow |
| plan / apply | sweep, orchestrator, scheduler, controller |
| assessment | decision, approval, ruling (for the record) |
| this repository | the archive |

The reverse also holds: do not file unrelated work under an existing word just
to avoid a new directory.

### Inheriting a term

Reuse a standard term before inventing one. Never accept a standard term's
looser meaning silently: either constrain it or use it only as a mapping. Always
write the prefix on the false friend: `arch:Entity` is not `prov:Entity`.
([ADR 0012](decisions/0012-inherited-meaning.md))

### Adding a noun

New nouns should be expensive. Before adding one, answer in a proposed decision
record:

1. What does it do that nothing else does?
2. Which verb does it own?
3. Which invariant does it enforce?
4. Why can it not be code inside an existing concept?
5. Does it own authoritative state?
6. Is it really a subsystem, or an operation, protocol, projection, library, or
   detail?

"Sweep" failed this test and became plan / apply.
