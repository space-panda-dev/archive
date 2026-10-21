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
- **an archive**: an actual body of archival data on disk. It lives outside
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
**Region entry**: a bank entry over `[start, end)` of its content (a song
span, widened), keyed by the region as well; a whole-content entry covers
every region. A **bank request** asks the bank for one key a reader found
missing; the **scheduler** (`enrich backfill`) makes requests good and fills
the bank by a mode (`songs`, `eager`, `on-demand`). An **equivalence
record** is the measured agreement that lets one entry **stand in** for
another made with other interchangeable parameters, such as a GPU's stems
for a CPU's; an **expensive** extractor is one whose recorded cost is above
the threshold. All are regenerable bank state.
([ADR 0112](decisions/0112-bulk-import-at-scale.md), [ADR 0090's
amendment](decisions/0090-extractor-runs-travel-through-spool.md).)
**Streaming mode**: an extractor run over an unbounded stream of audio
instead of one content, under the same manifest, id and version; a way to
*extract*, never writing the bank. Its "frames" are audio sample frames (one
sample per channel), not the `frames` extractor or its analysis frames. A
**stream event** is one line of streamed output: a row of a series, an item
of an events or spans output, or a scalar's running estimate. It is broader
than the `events` feature kind, which one stream event may carry, and it is
never admitted. The **live runner** (`enrich/live.py`) runs streaming
extractors and live tools over one stream for a rehearsal node and proposes
nothing. ([ADR 0108](decisions/0108-live-analysis.md))

## Deletion

([ADR 0004](decisions/0004-deletion.md))

**Retire**: take an object out of the archive's holdings; its records stay.
**Destroy**: remove content from the archive's custody and from every copy it
controls. Its **dependents** are the content made from it (the outputs of its
lineage claims) and the snapshot packs that hold or rebuild it; each needs a
recorded disposition ([ADR 0024](decisions/0024-retire-and-destroy-records.md), [ADR 0098](decisions/0098-content-lineage.md), [ADR 0101](decisions/0101-daw-projects.md)). **External survivor**: a known copy the archive cannot remove.
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
such as a recorder card. A capture **source** is a different word: one
device's stream of files within a session ([ADR 0070](decisions/0070-capture-import.md), [ADR 0107](decisions/0107-multi-device-and-live.md)).

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
are three of them: operator (RED), circle (GREEN, the circle and its
processors, since [ADR 0092](decisions/0092-internet-exposure.md); earlier claims' AMBER is shown as "circle
(tailnet only)"), and published (CLEAR).
**Reach**: how far an object may be shown or shared, for each use, computed
from the sensitivity and rights of everything it pins or was made from; a
view, never stored.
**May publish**: a view saying whether a publication approval still
permits publication on a given day, and what blocks it if not.
**Personal-use licence**: a rights statement that a subject was licensed to
one person for their own use, as a bought tablature is; it keeps the subject,
and what pins it, within the circle. No rights statement limits the circle,
which is private.
([ADR 0085](decisions/0085-documents-lyrics-rights.md))

**Use** (of a right): `listen` (hear or see it, without a copy to keep),
`derive` (make new work from it), or `redistribute` (pass copies on); a rights
claim may give each use its own furthest class. **Clearance**: one person's or
organisation's permission for one use of one subject, in one Work or
recording, for one destination class, such as a label clearing one sample in
one recording. ([ADR 0097](decisions/0097-rights-by-use.md))

**Ingress**: which way a request reached the relay, `tailnet` or `internet`;
only the tailnet can carry operator capacity. **Processor route**: a
transition recording, before anything is sent, that a destination is reached
through a processor (Cloudflare, for the internet ingress); the relay serves
the internet only while one holds. ([ADR 0092](decisions/0092-internet-exposure.md))

**Audience** (of a relay): a destination of class circle whose **members**
the archive records, each a person the operator named; who is in it is an
operator transition. It replaces the earlier "circle (of a relay)" configured
on the relay; *the circle* still names the exposure class. A caller's
**capacity** is `operator` or `member`; over the internet even the operator's
login is a member of the audiences that list it. The **readable set** is what
a member may read in one audience: what an approval to it covers, what that
pins, and members' own records over readable things; a caller in several
audiences reads the union. A record kept from a caller is **withheld**, and
an answer counts what it withheld. ([ADR 0088](decisions/0088-bandmates.md), [ADR 0093](decisions/0093-audiences.md))
**Work origin**: a claim whether a Work is the operator's own, co-written, or
others', naming its **writers**. **Sharing consent**: a co-writer's own
claim approving or refusing one destination for exact revisions. The
**sharing gate** lets a co-written Work's content beyond the band only with
every co-writer's consent. ([ADR 0088](decisions/0088-bandmates.md))

**Write grant**: an operator transition saying what one person may propose,
over which audiences, by **ability**: `annotate` (claims and judgments about
readable things), `cut` (selections and their lineage), or `admit` (staging
new content). A person's **effective grant** is their latest grant per
ability and audience, a view; where none names them, the **default** allows
`annotate` and `cut` and denies `admit`. A grant permits proposing and never
adds weight to a claim. A member may set their own sensitivity limit on
content that records them or that they deposited. ([ADR 0094](decisions/0094-peoples-writes.md))

**Origin**: where content came from and whose it is: a claim about an
Artifact or a recording with one value of a closed set (`own_capture`,
`participant_capture`, `commercial_release`, `sample_pack`,
`broadcast_or_web`, `generated`, `unknown`). Its **depositor** is the person
whose library the archive took the content from: custody, not authorship;
their limits bind as a recorded person's do. ([ADR 0096](decisions/0096-origin-of-recordings.md))
**Rights**: what may legally be done with it, recorded with terms and dates.

## Collections and meaning

**The archive's holdings**: everything an archive has admitted. Older records
call this "the collection"; *collection* now names an Entity kind (below).

**Physical item**: a thing in the archive's holdings whose bytes the archive cannot
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
eighth unchanged). **Tempo** is derived from bars and beats, never stored; a
set list's target tempo is a stated intention, not a tempo ([ADR 0106](decisions/0106-set-lists-and-clocks.md)).

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
is where the tune moves to another part. **Articulation**: how a written
note is played (staccato, staccatissimo, tenuto, accent, marcato, fermata),
as a document or a person wrote it. **Written rhythm**: a view writing a
notes claim's sounded notes on a meter's beat grid, each to the next onset,
short ones marked staccato and rests only at phrase boundaries. **Bass
line**: a notes claim marked `bass`, served as a line and a staff of its
own, never a recording's melody by default.
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
An **asserter tier** ranks a claim by who asserted it relative to its subject
(operator, participant, other member, machine), inside a named rule that uses
it; never stored. ([ADR 0095](decisions/0095-participation.md))

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
**locator scheme** (plain text or LRC). A word may hold **syllables**, each
by character offsets into the document's word, and a syllable may be
**carried by** the notes it is sung on (several for a melisma).
**Document source**: a claim of what
kind of document an Artifact is and where, and when, it was retrieved.
([ADR 0085](decisions/0085-documents-lyrics-rights.md))

**Sketch**: a Work-level melody with an **unmeasured** item, a pitch whose
length is not known; never laid in bars or pinned by a lead sheet.
**Underlay**: a claim placing a lyric document's syllables under a Work-level
melody's items, optionally under named **passes** of a repeat. An **open
repeat** is played as many times as a performance plays it, with a
**last-time ending** and a **default ending**; a recording's form spans name
the pass each span plays, and a span naming no section is a **departure**.
**Passage alignment**: a claim that a passage of one performance, or of a
Work's tune, corresponds to a passage of another, at a stated **precision**
(rough, approximate, exact); neither a capture alignment nor a document
alignment. **Folded chart**: a recording's chart with mapped passes folded
into repeat signs, a pass that differs shown as a **variant**; a view. A
**lyric link** is a syllable's notes or an underlay's items; **stale lyric
links** are those still pinning superseded notes, melody or lyric
documents; a view.
([ADR 0113](decisions/0113-editing-lead-sheets.md))

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
**Session scope**: a contribution about a whole `recording_session`.
**Participation**: a contribution at session scope naming a person who took
part. A **participant** is a person whose participation the operator asserted
or affirmed, computed per session and never stored; a **suggested
participant** is one a band membership places in the band on the session's
date, offered to the operator and never counted. ([ADR 0095](decisions/0095-participation.md))

**Tracklist**: a release's **media** and their **tracks**, in order, each
track a recording. **Recording audio**: a claim tying a recording Entity to
archived audio, whole or in part. **Catalogue correspondence**: a claim that
an artist, release, or recording is the same as, close to, or (for a
MusicBrainz release group or a Discogs master) narrower than an entry in an
outside catalogue (version 2 adds Wikidata and a Work subject,
[ADR 0105](decisions/0105-notebook.md)). **Credits per track**: the view of which credits apply to
one recording, including those of its release.
([ADR 0087](decisions/0087-tracklists-and-correspondence.md))

## Lineage and samples

**Lineage claim**: a claim that one output Artifact was made from exact
inputs (Artifacts or selections), optionally by a machine's run; reach and
destroy follow it. A **lineage span** says where part of an input landed in
the output, with the **transforms** done to it from a closed list; the word
*placement* stays a managed copy's. ([ADR 0098](decisions/0098-content-lineage.md))

**Sample**: a selection, or a whole Artifact such as a library one-shot,
viewed as something to play or reuse; a view, not a new kind of record. It is
not an audio sample (one value at a sample rate), which keeps the plain word
in sample positions and code. A selection's **signal** is the stem or channel
it cut, and its **recipe** says, precisely enough to render, what is done to
the cut; a lineage claim with a recipe reads as its source. A **sample
render** is the file a recipe makes, regenerable and never evidence until
chosen or sent, named by the **sample key** of its **sample request**.
**Sample form** (`one_shot`, `loop`, `break`, `phrase`, `texture`, `fx`),
**sample sound** (instruments, or the asserter's word), and **sample tag**
(one free word per claim) are claims that classify a sample; none is ranked.
([ADR 0099](decisions/0099-samples.md))

## Libraries, projects, and notebooks

**Collection**: an Entity of kind `collection`, a set of items of any media
as a person gathered it (a friend's crate, a sampler's card), neither a
release nor a session. Its members are asserted by **membership claims**,
each optionally with a **slot** (a pad or bank position); a sample pack is a
release. A **source-path claim** keeps the path a file was found at, as
history, never as a location. A **bank grammar** reads one sampler's card
layout. ([ADR 0100](decisions/0100-packs-and-collections.md)) A **pad map** is a
claim on a sampler-bank collection saying, as one device was set up at one
time, which sample each pad holds, which bank it is in, what MIDI note fires
it, and how it plays; a proposer reads a session's MIDI through it into
lineage. ([ADR 0111](decisions/0111-pad-maps.md))

**DAW project**: an Entity of kind `daw_project`, one production across its
saves. A **project snapshot** is a claim that a **snapshot pack** (a
deterministic pack of the project folder, whole, or slim without its audio)
is a kept state of the project, and which of its members are which audio and
MIDI Artifacts. **Snapshot** as a noun means only this. A **bounce** is an
Artifact whose lineage names a snapshot pack. ([ADR 0101](decisions/0101-daw-projects.md))

**Notebook**: a collection whose members are **pages**, each a document
Artifact with a source path, revised as the file changes; *page* avoids
*note*, which means note events. Snapshotting a notebook is an operation, not
a noun: `capture notebook` admits the pages that changed. A **notebook link**
is a claim that a page, at a locator, is about an archive object; a **page
link** is the view of links between pages. ([ADR 0105](decisions/0105-notebook.md))

## Search

**Index**: World's second store, disposable, built from a World generation
and the feature bank for filtering, similarity, and text search over
**units** (a selection, an audio Artifact, a transcript segment, or a
notebook page). Door, policy, and transitions never read it. A **facet** is
one named value of a unit with the revisions it depends on; an **embedding**
is a vector measured from content so that similar sounds lie near each other,
a bank series never served; an **index generation** fixes which World
generation and bank entries one build read. ([ADR 0102](decisions/0102-retrieval-index.md))

## Voice and rehearsal

**Voice note**: a claim that a recording, or a span of one, is a spoken note,
and by which route it arrived (`voice_memos`, `browser`, `shared_file`, or
`within_recording`); it is never forced into a session. A **voice-source
reader** reads what a source application states about one file.
([ADR 0103](decisions/0103-voice-capture.md)) **Capture start**: a claim of when a file's recording began,
which may be unknown; an Artifact cannot say it. ([ADR 0107](decisions/0107-multi-device-and-live.md))

**Voice consent**: a person's own claim whether the archive may recognise
their voice, and from which audio; only they assert or withdraw it. A
**speaker span** says who is speaking over some samples, naming only a person
whose consent is live, or an **unknown speaker** numbered within one claim. A
**voiceprint** is the embedding of one person's voice that recognition
compares with; regenerable, never served. ([ADR 0104](decisions/0104-transcription-and-speakers.md))

**Set list**: a person's claim of what they intended a session to hold, in
**entries**, each a Work with targets such as a **target tempo**; evidence of
intent, compared with what was played by a view. A **clock** is a non-person
agent (a `process` Entity) that kept time at a session, such as a metronome
or a MIDI clock, and asserts its own meter claim. A matcher run's **prior** is
the set list it used to widen its candidates. ([ADR 0106](decisions/0106-set-lists-and-clocks.md))

**MIDI stream**: a claim giving a MIDI file a sample timeline and bounds,
beside `audio_stream`. **Rehearsal node**: a client program, a sibling
application in its own repository like the review app, never Archive code
([ADR 0109](decisions/0109-rehearsal-node.md)), that records at a rehearsal and holds the operator's **live responses**
(ordinary claim drafts) in an **outbox** of **response groups** anchored to a
recording not yet admitted; the importer flushes it. A **practice take** is an
Artifact with a capture origin and lineage to what it was played over.
([ADR 0107](decisions/0107-multi-device-and-live.md)) A **clock track** is the
first track of a rehearsal node's MIDI capture, carrying the tempo map of the
node's clock (tempo, time signature and start and stop markers) in MIDI's own
terms; the clock's meter claim is read from it.
([ADR 0110](decisions/0110-shared-clock.md))

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
| the archive's holdings | the collection |
| audience (a relay's group of people) | circle (that is the exposure class) |
| lineage span | placement (that is a managed copy's) |
| page (of a notebook) | note (that is a note event) |
| project snapshot | snapshot, for anything else (snapshotting is an operation) |

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
