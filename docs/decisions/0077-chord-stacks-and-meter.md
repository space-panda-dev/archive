# 0077. Chords are stacks and meter is a timeline, each asserted by someone

Status: accepted (by the operator, 2026-10-18); amended before release

**Amended.** Importing one real album's chord and meter sources, before any
specification release installed these contracts, showed four gaps, so this
record is amended in place rather than superseded:

1. **Beat-only layers.** A beat tracker gives beats, sometimes downbeats, and
   no time signature; its claim had to write each beat as a placeholder 1/4
   bar, asserting a meter nobody stated. A layer may now hold `beats` instead
   of `bars`.
2. **Modulation kinds.** A link's `ratio` was fully determined by its note
   values and did not say what the equation meant, so a chart that regroups
   4/4 into 12/8 with the eighth unchanged could only write "eighth = eighth,
   1:1", which says nothing changed. A link now states its `kind`, an
   `equation` or a `regrouping`, and the ratio is derived, not stored. A feel
   written over the notated meter is a layer with the role `felt`.
3. **Held span ends.** A transcription's chord names, like many sources, give
   a start and no end; the importer held each until the next, and the claim
   could not say that the end was the importer's. A span may now mark its end
   `held`, under the claim's stated `hold_rule`.
4. **Computed names.** A raised fifth over an omitted fifth was named a flat
   thirteenth, a single note was given the symbol of a power chord without
   its fifth, and a 6/9 chord over a bass printed two slashes. These are
   views, so no claim changes; World's names are corrected.
5. **Untimed chord sequences.** A chart with no bar lines or times, the
   commonest kind being chords written over lyrics, could not be stated: it
   says which chords come in which order and roughly where in a line or
   section they fall, never when. Its importer kept those charts outside any
   claim. A new sibling contract, `archive.claim/chord_sequence` version 1,
   holds them; a timed claim placed from one cites it, and a correction
   cites what it corrects. The operator approved this change.

Nothing in an archive can say what chord sounds at a moment of a recording,
or where its bars and beats fall. The feature bank proposed by ADRs 0073 and
0074 measures chords and beats from the mix, but as regenerable features, in
a fixed vocabulary of 24 triads and a single beat grid, and never as claims.
The operator's lead-sheet generator stores a chord as an opaque string such as
`"Cm7"` at a beat position "0-3 for 4/4 time", with `4/4` as the default meter.
Neither can hold what the operator wants kept: chords that resist naming,
charts and transcriptions that disagree, and meters that change, group
unevenly, modulate, or run against each other.

The operator decided the following on 2026-10-18; this record states it as
given.

- **A chord is a stack**: "a bass (independent of the root), an optional or
  ambiguous root, and the set of pitches above — clusters, quartal chords,
  upper structures are all fine". "There's some chords that even this system
  will have trouble naming ... we should be able to express that ambiguity."
  Names, in Harte syntax or as lead-sheet symbols, are computed views of the
  stack, and one stack may have several valid names (Cm7/G = Eb6/G). A stack
  may carry its voicing: the actual pitches, with octave. The source's own
  text is kept verbatim (for example `Gm(maj13)/D`). Ambiguity is expressed as
  weighted candidate readings per span, and as uncertain tones ("a 7th is
  present, which one is uncertain").
- **Meter is a timeline**: bars with a numerator and denominator, a beat
  grouping (such as 2+2+3), and beat times; an explicit metric-modulation link
  between bars carrying the pulse ratio (dotted quarter = quarter, 3:2); and
  several simultaneous layers for polymeter and cross-rhythm. Tempo is
  derived, not primary.
- **Every value is asserted by someone**: a machine (a derivation run with
  parameters, ADR 0064), an outside source aligned to the audio (a Guitar Pro
  transcription or a chart from a website, with its URL, file digest, bar
  number, and alignment method), or a person. All coexist as claims and
  nothing is overwritten (ADR 0005); a current or resolved view is computed by
  a stated rule and records why. Careful analysts have derived different
  stacks from the same audio of a famously disputed opening chord, so
  competing readings stay attributed.
- **Mix first**: machine extractors must work on the full mix; claims derived
  from stems are later, better-sourced additions beside them, never
  replacements.
- **A future use**: compare simultaneous stacks from different sources, such
  as two players' instruments, and flag spans where they disagree on chord
  tones (one plays a major 7th, the other a minor 7th).
- **Exchange formats**: export lossy views to JAMS (`chord` and
  `chord_harte`, `beat_position`, `tempo`, `key_mode`) and Harte strings, and
  to MusicXML `<harmony>`, noting what each cannot express.

## Decision

**Two timed claim contracts, each one asserter's reading of one audio
stream,** and an untimed one, the chord sequence (below). The two timed ones
pin the audio whose timeline they describe the way span suggestions do
(ADR 0066), and use integer samples at that stream's rate (ADRs 0003, 0065):

- exactly one `source` Artifact revision: the audio the samples count, which
  is the mix;
- exactly one `stream`, an `archive.claim/audio_stream` version 1 Claim
  revision about that same Artifact revision; the payload's `sample_rate`
  equals the stream's, and no span or bar ends past its `sample_count`;
- at most one `run`, an `archive.claim/derivation_run` version 1 Claim
  revision, which a machine's claim pins (ADR 0064);
- at most one `document`, the Artifact revision of an outside source's bytes
  (a Guitar Pro file, a saved chart page);
- zero to eight `analysed` Artifact revisions: audio the asserter analysed in
  place of the mix, such as a separated stem, whose samples share the
  source's timeline;
- for a chord stacks claim only, at most one `sequence`, the
  `archive.claim/chord_sequence` version 1 Claim revision an alignment placed
  on this recording, and zero to eight `corrects`, chord claim revisions this
  claim corrects (below).

A claim that pins a `document` or a `sequence` states its `alignment`: a
`method` of 1 to 200 characters (`manual` for a person's placement,
otherwise the aligning method's identifier) and, optionally,
`max_error_samples`, as `capture_alignment` does (ADR 0048). It may give the
`document_url` it was retrieved from, an http or https URL of at most 2,000
characters. The URL is where it came from, never the evidence: the pinned
Artifact is (ADR 0042). Spans and bars of such a claim may carry a
`locator`, the source's own bar number (and, for a chord, beat), each a
positive integer. A claim without a document has no URL or locator, and one
that pins neither a document nor a sequence states no alignment.

**`archive.claim/chord_stacks` version 1.** Its payload has `sample_rate`,
optionally `part` (1 to 100 characters naming the part the stacks describe,
such as a document's track or an instrument, when not the whole sound),
optionally `weight_scale` (1 to 200 characters naming what weights mean in
this claim), optionally `hold_rule` (below), and `spans`: 1 to 10,000
objects of `start_sample`, `end_sample`, `readings`, optional `end`,
optional `locator`, and optional `entry`, ordered and not overlapping, as ADR
0066's spans are. A gap between spans asserts nothing, so a claim need not
cover the recording: a person's three corrected chords in an hour-long
improvisation are a whole claim. A span's `entry`, allowed only when the
claim pins a `sequence`, is the index of the sequence entry it was placed
from; a repeated entry is placed once per time it is played.

A span's `end` is `asserted` (the default when absent: the asserter says
where the chord stops) or `held`: the source gave only a start, and the
asserter held the chord until the next change by its own rule. A held span
still has its `end_sample`, so spans order and tile as before. A claim states
its `hold_rule`, 1 to 500 characters saying how it held (for example "until
the next name, at most to the end of the following bar"), exactly when one of
its spans is held. A held end is weaker evidence than an asserted one: it
says only that the source wrote nothing new before then. A consumer, and any
later current rule, must not weigh where a held span stops as it weighs an
asserted end; another claim's chord over the held stretch is not a
contradiction of the same strength.

A span's `readings` are 1 to 16 distinct candidates in the asserter's order of
preference, each in one of three forms:

- a **stack**: `bass`, a pitch class; `tones`, the distinct pitch classes
  sounding above the bass in ascending order (0 to 12 of them, including the
  bass's own class only when it is doubled above); optional `root`, a pitch
  class that need not sound (a rootless voicing names one that does not), and
  whose absence means the asserter names no root; optional `uncertain`, 1 to
  4 groups of `pitch_classes` and `present`, where `exactly_one` (at least two
  classes) says one of them sounds and `maybe` says any one or none may, and
  no uncertain class is the bass, a tone, or in another group; optional
  `voicing`, 1 to 32 distinct ascending MIDI note numbers whose lowest is the
  bass and whose upper notes' classes include every tone and nothing outside
  the tones and uncertain classes;
- **no chord**: `no_chord` true, for a source or listener asserting silence
  of harmony ("N.C."), which differs from asserting nothing;
- **text only**: the source's `text` when the asserter keeps it without
  interpreting it as a stack.

Every form may carry the source's verbatim `text` (1 to 200 characters) and,
exactly when the claim states a `weight_scale`, a `weight` as exact decimal
text. A weight compares readings within its claim only; there is no scale
across claims (INV-8). Pitch classes are integers 0 to 11 with C = 0, in
twelve-tone equal temperament; the spelling of a name is a view.

**`archive.claim/chord_sequence` version 1: chords in order, with no
times.** A chord chart over lyrics, a cue sheet ("intro: Gm7 C9 x4"), or a
person saying "the bridge goes to Gmaj7#11" says which chords come in which
order and roughly where in a line or section they fall, but never when. Such
a claim's subject is what it describes, not a recording: it pins at least one
of a `work` (an Entity revision of kind `musical_work`) and a `document` (the
Artifact revision it was read from), at most one `run` when a tool read it,
and zero to eight `corrects`, the sequence claims it corrects. It pins no
audio and has no `sample_rate` and no `alignment`. Its payload has optional
`part`, `weight_scale`, and `document_url` (as above), `entries`, and
optional `repeats`:

- `entries`: 1 to 10,000 objects in the source's written order, each with
  `readings` in exactly the form and rules of a chord stacks span (a stack,
  `no_chord`, or text only, with the source's own spelling verbatim in
  `text`, weights, uncertain tones, and voicing), an optional `section` (1 to
  100 characters, the source's own section label, such as "Verse"), and,
  when a document is pinned, an optional `locator`: the document's `line`
  and at most one of `word`, `syllable`, or `column` within that line, each
  a positive integer. Located entries never go backwards in the document.
- `repeats`: ranges of entries the source marks as repeated, each `first`
  and `last` entry index and `times` (2 to 999), ordered by first entry with
  the outer of two ranges first; two ranges nest or are apart, never cross.
  The played order is a view: every repeat unrolled.

**Lyrics stay out.** A locator points at a line and a word of a document; the
claim never copies the line's words. Lyrics are the document's, admitted as
its Artifact under that Artifact's own sensitivity, and a sequence claim
holds no lyric text, in `text`, `section`, or anywhere else. A `section`
label is the source's structural name, never a quoted line.

**From a sequence to a recording.** An alignment run that places a sequence
on a recording makes a separate chord stacks claim about that recording: it
pins the `sequence` and the recording's `source` and `stream`, states its
`alignment` method and, when it can bound it, `max_error_samples`, and
gives each span the `entry` it came from. Each span's end is `held`, since a
sequence gives no ends. The sequence stays untimed and untouched, and one
sequence may be placed on many recordings, each placement its own claim.
Where a person's names differ from what a machine hears over the same
seconds (an added ninth the machine's triads cannot hold), both stay as
separate claims, and the disagreement is data.

**Corrections, and the resolved view.** A correction is its own claim, made
by its own asserter (usually a person in a review client), and may pin in
`corrects` the claims it corrects. It replaces nothing (ADR 0005). The
resolved view of a recording's chords, computed by World and never stored,
has one stated rule: over the samples where a claim's spans overlap the
spans of a claim it names in `corrects`, the correcting claim's reading is
shown first and the corrected claim's beside it, marked as corrected by that
claim; elsewhere the corrected claim stands, so a partial correction
corrects only what it covers. The rule follows the citation, not the kind of
asserter (INV-8). Where two claims correct the same claim over the same
samples and disagree, or claims correct each other, the view reports the
conflict and chooses neither (INV-24). Without a citation the view chooses
nothing; asking for the current chord stays unsupported.

**How this fits the scopes of contributions (ADR 0078).** A sequence about a
Work is that ADR's `work` scope: it describes the composition, and says
nothing about any one recording's samples, as a Work's writers say nothing
about who played. A timed chord stacks claim is its `span` scope: sample
ranges of one stream. Release and recording scopes have no chord claim. The
step from a Work to a recording is never implied by containment; it is an
alignment, made and attributed as its own claim.

**Toward notated output.** Lead sheets, then printable parts, exported later
to MusicXML or LilyPond, need nothing these contracts forbid, and no exporter
is built. A lead sheet needs a sequence or timed chords, a meter layer with
bars and locators to place them in bars, a melody (no contract holds one),
and the lyrics (from the document, under its own sensitivity, joined at
export and never copied into a claim). A part needs claims with a `part`
naming its instrument, stacks with `voicing` for its notes, and note
durations and rhythms, which a later contract would add. The `part` field and
per-part claims, which the Guitar Pro importer already makes, are what that
later work reads.

**`archive.claim/meter_layers` version 1.** Its payload has `sample_rate`,
`layers` (1 to 8), and optional `modulations`. Layers are simultaneous and
none is primary; each has optional `label` (1 to 100 characters), optional
`role`, and exactly one of `bars` or `beats`.

A **bars layer** has 1 to 10,000 bars, ordered and not overlapping (a gap is
a stretch with no asserted meter). A bar has `start_sample`, `end_sample`,
`numerator` (1 to 64), `denominator` (1, 2, 4, 8, 16, 32, or 64), optional
`grouping` (positive integers summing to the numerator, such as `[2, 2, 3]`),
optional `beats` (the sample of each counted beat: one per group, or one per
numerator unit without a grouping; the first is the bar's start and each is
later than the one before and within the bar), and optional `locator`. A bar
always asserts its time signature: a bar is that assertion.

A **beat-only layer** asserts beats and nothing about the bar: `beats`, 1 to
100,000 distinct ascending samples within the stream; optional `downbeats`,
those of its beats the asserter marks as downbeats (present, the beats not
in it are asserted not to be downbeats; absent, the layer says nothing about
downbeats); and optional `beat_unit`, the beat's note value, which is
unknown unless the asserter states it. It has no numerator, denominator,
grouping, or locator. A beat tracker's claim is one; when it also finds
downbeats it marks them, and it never invents a bar to hold them.

A layer's `role` is `written` (the meter as notated) or `felt` (a feel heard
over it); absent, the layer does not say. A feel written over the notated
meter, such as a triplet feel in 4/4, is a `felt` layer over the same samples
as a `written` one: 12/8 bars grouped `[3, 3, 3, 3]` against 4/4 bars, or
swung sixteenths as 24/16 grouped in threes. A claim with a `felt` layer has
at least one `written` layer.

A **modulation** joins bar `bar` of a bars layer `layer` to the bar before
it, which must end where it starts, and states its `kind`:

- `equation`: a true metric modulation. Its `before` and `after` note values
  (each a `denominator` and optional `dots`, 0 to 2) say that the note value
  `before` under the old tempo lasts as long as the note value `after` under
  the new one, so the tempo changes by the ratio of their lengths: dotted
  quarter = quarter is 3:2, and eighth = eighth keeps the tempo.
- `regrouping`: every note value keeps its length and the felt pulse moves,
  from `pulse_before` to `pulse_after`, which differ. A song that regroups
  4/4 into 12/8 with the eighth unchanged is a regrouping from the quarter to
  the dotted quarter; the tempo does not change.

The ratio is never stored: it is derived from the kind and the note values.
Tempo is never stored either; it is derived from bar and beat times.

**Names are views.** Given a stack (bass, tones, optional root), the names
are computed: every sounding pitch class is a candidate root unless a root is
asserted, and each candidate gives a strict Harte string (Harte's 16
shorthands, extensions, `*` omissions of only the root and fifth, or a plain
interval list when no shorthand is nearer) and, when an exact quality is
known, a lead-sheet symbol. The order of names is a stated rule, not a
judgment. Uncertain tones give one set of names per resolution. Names are
never stored as the claim; an asserter's own name is its `text`. An added
tone is named by what sounds, not by the shorthand it is added to: a raised
fifth with no perfect fifth is a `#5` (C E G# B is `C:aug(7)`), and only
above a sounding fifth a `b13`. A single pitch class is Harte's lone-note
list, `Eb:(1)`, with the symbol `Eb(1)`. A symbol's only slash introduces its
bass, so six-nine is written `6(9)`: `Bb6(9)/G`.

**Mix first.** A machine extractor makes its chord and meter claims from the
mix. A claim from a stem is another claim about the same `source`, pinning the
stem in `analysed`; it never supersedes the mix's claim, and nothing ranks it
above it by kind (INV-7, INV-8).

**Current is computed, and not yet defined.** These contracts define no
current rule. Any later rule is a stated policy that names the claim it
chose and why, and reports a conflict rather than choosing where it cannot
(INV-24). Until one is accepted, asking for the current chord or meter is
unsupported.

**Exchange is lossy and says so.** World's views export a chord stacks claim
to JAMS `chord_harte` (or `chord`, which also allows `X`) as one annotation of
the readings at one rank, with the weight as confidence when the claim's
`weight_scale` is `probability`; a bars
layer to `beat_position` and a derived `tempo` (quarter notes per minute,
per bar); and a beat-only layer to `beat` (1 at a marked downbeat, null
elsewhere) and a derived `tempo` per beat interval (quarter notes per minute
when the beat unit is stated, otherwise beats per minute). JAMS has no field
on an observation for a held end, so a chord export puts the claim's
`hold_rule` in the annotation's free-text `annotation_rules` and the held
observations' indices in its sandbox; a reader of the data alone sees every
end as asserted. Each annotation lists in its sandbox what it dropped:

| Format | Cannot express |
|---|---|
| Harte | an unnamed root's ambiguity, alternative readings, uncertain tones, voicing and register, confidence, the source's text, a held end (Harte has no time) |
| JAMS `chord_harte` / `chord` | as Harte, plus a reading with no stack (`chord_harte`) and weights on any scale but a probability; a held end survives only in `annotation_rules` and the sandbox |
| JAMS `beat_position` | a grouping (kept only as beat positions, 1, 3, 5 for 2+2+3), modulations and regroupings, a layer's role, simultaneous layers (one per annotation), bars without beat times (downbeat only) |
| JAMS `beat` | a beat unit, a layer's role, simultaneous layers; beats that are not downbeats carry no position |
| JAMS `tempo` | anything but a number per time range; it is derived here, and for a beat-only layer without a beat unit it counts beats, not quarter notes |
| JAMS `key_mode` | nothing to export yet: no key contract exists |
| MusicXML `<harmony>` | ambiguity, weights, uncertain tones, a rootless reading's missing root, and a time position in samples; a voicing belongs in notes, not `<harmony>` |

JAMS has no namespace for a time signature, a grouping, a metric
modulation or regrouping, or a felt meter. A MusicXML export maps a stack's chosen name to `<root>`,
`<kind>` (with the lead-sheet `text`), `<bass>`, and `<degree>` additions and
subtractions, and is not built yet.

**The rules live in World until a release installs them.**
`world/harmony.py` holds every rule above as World will re-check it when it
projects these claims, the computed names, and the JAMS views. It uses only
the standard library, so an importer or extractor can check a proposal
before Door does. Door cannot admit any of the three contracts until a
specification release installs their bundles; the release adds the Door
profile rules this record needs (ascending distinct integer arrays, a
grouping that sums to its numerator, beats within their bar, a beat-only
layer's ascending beats and downbeats among them, a `felt` layer only beside
a `written` one, a held end only with a `hold_rule`, a sequence's subject,
located entries in document order and repeats that nest, a span's `entry`
only with a pinned `sequence`, modulations between adjacent bars of a bars
layer whose kind fixes their fields, and the existing sequential-span,
target-measure, and same-subject rules applied to these arrays), and World's
projection of all three. [Chord and meter
sources](../chord-and-meter-sources.md) describes how the first importers
will assert.

**New nouns.** Chord stack, reading, uncertain tone, voicing, computed name,
held end, chord sequence, sequence entry, correction, meter layer, bar,
beat-only layer, beat grouping, felt layer, metric modulation, regrouping,
document, locator, document alignment, and mix enter the
[vocabulary](../vocabulary.md). None is a subsystem: each names part of a
claim's payload or references, or a view of one. They own no verb beyond
Annotation's and Enrichment's *assert* and *derive*, enforce no new
invariant, and hold no authoritative state outside the claims Door admits,
so they live in three contracts and a World module rather than a new
concept.

## Consequences

- Two charts, a transcription, a listener, and a machine can each say what
  sounds over the same seconds, each attributed, and none is lost when another
  is added. A disagreement is visible as overlapping spans whose readings
  differ.
- The future tone comparison reads two chord stacks claims over the same
  source and compares tone sets where spans overlap. It needs no contract
  change, only stacks from two `analysed` stems or two people.
- A source's meter can align its chords: an importer that knows the source's
  bars asserts a meter layers claim with `locator`s and places each chord at
  the samples of its bar and beat.
- A machine's beat grid and a source's bars are different assertions: a
  beat-only layer never pretends to know the meter, and matching it to a
  bars layer is the open question of matching beat grids.
- Two journeys the operator described are served without overwriting
  anything. An improvisation on unpublished material starts with machine
  claims alone; over months the operator corrects pieces of it in time in a
  review client, each correction a partial claim citing the machine claim it
  corrects, and only pieces are ever covered. A song written years ago and
  recorded many times by several ensembles starts as the operator's own
  document of lyrics with chords above: imported as a sequence about the
  Work, then aligned to each recording, where it locks onto the machine
  claims and differs from them where the person wrote other extensions. Over
  time it gains timed corrections, full lead sheets, and later parts.
- No invariant changes. INV-7 keeps every reading, and INV-8 keeps weights
  in their own claim's terms.
- Pitch classes assume twelve-tone equal temperament. A detuned recording is
  still named in that system; the tuning itself is not part of this
  contract.

## Alternatives considered

- **A fixed chord vocabulary** (24 triads, or a longer list of qualities). A
  cluster, a quartal stack, or an upper structure would have to be forced into
  a name it is not, and the forcing would be invisible afterwards.
- **Harte strings as the stored form.** Harte cannot say that a root is
  ambiguous, that one of two sevenths sounds, which octave a note is in, or
  how sure the asserter is; and the same stack has several Harte strings, so
  two equal readings would look different.
- **JAMS as the stored form.** JAMS has no time-signature, grouping, or
  modulation namespace, measures time in float seconds rather than samples,
  and its annotation metadata is free text rather than pinned provenance.
- **MusicXML as the stored form.** It is a score format: positions are in
  divisions of a written measure, not samples of a recording, and
  `<harmony>` has no ambiguity or weights.
- **The lead-sheet generator's opaque strings with a hard-coded 4/4.** A
  string cannot be compared tone by tone, two spellings of one chord look
  different, and a 7/8 bar or a 12/8 change cannot be written at all.
- **One claim per chord span.** Thousands of records per recording, each
  repeating the same source, stream, and run (ADR 0066 rejected the same).
- **An optional time signature on a bar** (amendment), in place of a
  beat-only layer. A tracker without downbeats has no bars to hold its beats,
  so it would still need one invented bar per beat or one bar for the whole
  recording, and a bar would no longer always mean a stated meter.
- **A stored `ratio` beside the note values** (as first decided). It repeats
  what the note values determine, and it cannot say whether the tempo
  changed or only the pulse.
- **A sequence as a form of chord stacks claim** (amendment), rather than a
  sibling contract. A chord stacks claim's subject is a recording's stream
  and its spans are samples; a sequence has neither, so every rule of the
  timed contract would branch on the form, and a Work-scoped claim would
  share a contract with a span-scoped one.
- **Copying lyric lines into a sequence** to locate its chords. A line and
  word number locate as well, and the words stay in the document, under its
  own sensitivity.
- **A separate claim per meter layer.** Layers counted against each other by
  one asserter are one reading; splitting them loses that they were asserted
  together. A second asserter's layer is a second claim.
