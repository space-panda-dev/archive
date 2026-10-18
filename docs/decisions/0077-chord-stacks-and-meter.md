# 0077. Chords are stacks and meter is a timeline, each asserted by someone

Status: accepted (by the operator, 2026-10-18)

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

**Two claim contracts, each one asserter's reading of one audio stream.** Both
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
  source's timeline.

A claim that pins a `document` states its `alignment`: a `method` of 1 to 200
characters (`manual` for a person's placement, otherwise the aligning method's
identifier) and, optionally, `max_error_samples`, as `capture_alignment` does
(ADR 0048). It may give the `document_url` it was retrieved from, an http or
https URL of at most 2,000 characters. The URL is where it came from, never
the evidence: the pinned Artifact is (ADR 0042). Spans and bars of such a
claim may carry a `locator`, the source's own bar number (and, for a chord,
beat), each a positive integer. A claim without a document has none of these
fields.

**`archive.claim/chord_stacks` version 1.** Its payload has `sample_rate`,
optionally `part` (1 to 100 characters naming the part the stacks describe,
such as a document's track or an instrument, when not the whole sound),
optionally `weight_scale` (1 to 200 characters naming what weights mean in
this claim), and `spans`: 1 to 10,000 objects of `start_sample`,
`end_sample`, `readings`, and optional `locator`, ordered and not
overlapping, as ADR 0066's spans are. A gap between spans asserts nothing. A
span's `readings` are 1 to 16 distinct candidates in the asserter's order of
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

**`archive.claim/meter_layers` version 1.** Its payload has `sample_rate`,
`layers` (1 to 8), and optional `modulations`. Layers are simultaneous and
none is primary; each has optional `label` (1 to 100 characters) and `bars`:
1 to 10,000 bars, ordered and not overlapping (a gap is a stretch with no
asserted meter). A bar has `start_sample`, `end_sample`, `numerator` (1 to
64), `denominator` (1, 2, 4, 8, 16, 32, or 64), optional `grouping` (positive
integers summing to the numerator, such as `[2, 2, 3]`), optional `beats`
(the sample of each counted beat: one per group, or one per numerator unit
without a grouping; the first is the bar's start and each is later than the
one before and within the bar), and optional `locator`. A modulation joins
bar `bar` of layer `layer` to the bar before it, which must end where it
starts; it carries the `before` and `after` note values (`denominator` and
optional `dots`, 0 to 2) and the `ratio` of their lengths in lowest terms:
dotted quarter = quarter is `[3, 2]`, and a ratio that disagrees with its
note values is refused. Tempo is never stored; it is derived from bar and
beat times.

**Names are views.** Given a stack (bass, tones, optional root), the names
are computed: every sounding pitch class is a candidate root unless a root is
asserted, and each candidate gives a strict Harte string (Harte's 16
shorthands, extensions, `*` omissions of only the root and fifth, or a plain
interval list when no shorthand is nearer) and, when an exact quality is
known, a lead-sheet symbol. The order of names is a stated rule, not a
judgment. Uncertain tones give one set of names per resolution. Names are
never stored as the claim; an asserter's own name is its `text`.

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
`weight_scale` is `probability`, and a
meter layer to `beat_position` and a derived `tempo` (quarter notes per
minute, per bar). Each annotation lists in its sandbox what it dropped:

| Format | Cannot express |
|---|---|
| Harte | an unnamed root's ambiguity, alternative readings, uncertain tones, voicing and register, confidence, the source's text |
| JAMS `chord_harte` / `chord` | as Harte, plus a reading with no stack (`chord_harte`) and weights on any scale but a probability |
| JAMS `beat_position` | a grouping (kept only as beat positions, 1, 3, 5 for 2+2+3), metric modulation, simultaneous layers (one per annotation), bars without beat times (downbeat only) |
| JAMS `tempo` | anything but a number per time range; it is derived here |
| JAMS `key_mode` | nothing to export yet: no key contract exists |
| MusicXML `<harmony>` | ambiguity, weights, uncertain tones, a rootless reading's missing root, and a time position in samples; a voicing belongs in notes, not `<harmony>` |

JAMS has no namespace for a time signature, a grouping, or a metric
modulation. A MusicXML export maps a stack's chosen name to `<root>`,
`<kind>` (with the lead-sheet `text`), `<bass>`, and `<degree>` additions and
subtractions, and is not built yet.

**The rules live in World until a release installs them.** `world/harmony.py`
holds every rule above as World will re-check it when it projects these
claims, the computed names, and the JAMS views. It uses only the standard
library, so an importer or extractor can check a proposal before Door does.
Door cannot admit either contract until a specification release installs
their bundles; the release adds the Door profile rules this record needs
(ascending distinct integer arrays, a grouping that sums to its numerator,
beats within their bar, modulations between adjacent bars whose ratio
matches their note values, and the existing sequential-span, target-measure,
and same-subject rules applied to these arrays), and World's projection of
both. [Chord and meter sources](../chord-and-meter-sources.md) describes how
the first importers will assert.

**New nouns.** Chord stack, reading, uncertain tone, voicing, computed name,
meter layer, bar, beat grouping, metric modulation, document, locator,
document alignment, and mix enter the [vocabulary](../vocabulary.md). None
is a subsystem: each names part of a claim's payload or references, or a
view of one. They own no verb beyond Annotation's and Enrichment's *assert*
and *derive*, enforce no new invariant, and hold no authoritative state
outside the claims Door admits, so they live in two contracts and a World
module rather than a new concept.

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
- **A separate claim per meter layer.** Layers counted against each other by
  one asserter are one reading; splitting them loses that they were asserted
  together. A second asserter's layer is a second claim.
