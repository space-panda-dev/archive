# 0116. iReal Pro charts are documents read into a Work's untimed claims, linked by a person, exported as a view, and aligned to recordings

Status: proposed (2026-10-22). Builds on ADRs
[0077](0077-chord-stacks-and-meter.md), [0079](0079-machine-harmony-claims.md),
[0080](0080-key-and-tuning.md), [0083](0083-form-and-work-meter.md),
[0084](0084-lead-sheets.md), [0085](0085-documents-lyrics-rights.md),
[0087](0087-tracklists-and-correspondence.md) and
[0089](0089-practice-audio-and-exports.md); on acceptance it amends ADR
0089's format table, and adds a field to ADR 0113's meter layers version 3
before that version is installed (below). Nothing here is installed.

## Context

The operator keeps iReal Pro playlists: files holding one `irealb://` link
with many charts, each chart a title, a composer, a style, a key, a tempo
and a bar grid of chord symbols with sections, repeats, endings, segno and
coda. They want them in the archive as evidence, read into the claims that
already hold a Work's chart (sequence, untimed meter, form, key, credits,
lead sheet), linked to the Works they are of, written back out to iReal, and
used as an independent reference for what the machine hears in recordings.

[Chord and meter sources](../chord-and-meter-sources.md) sketched "a chart
importer" before untimed meter and form existed (ADR 0083): it asserted a
`meter_layers` claim only once placed on a recording. Specification 0.0.10
now holds a Work's bars without samples, so a chart can be read in full
before any recording is known. What is still missing: where the importer
lives, what its symbol grammar is, a home for a chart's style and tempo,
how a chart is tied to a Work when a title is never identity
([ADR 0042](0042-work-correspondence.md)), an iReal format among ADR 0089's
exports, and the alignment ADR 0077 designed but nobody built.

## Decision

### 1. The importer

**What is admitted verbatim.** Two kinds of document Artifact, each with an
`archive.claim/document_source` version 1 claim (ADR 0085), media type
`text/plain` (the link is ASCII; no new media type is needed), `kinds`
`["chords"]`:

- **the playlist**: the file's bytes exactly as given; its source claim
  keeps `filename` and, when the operator states it, `retrieved_at`. Agent:
  the importer's Package URL, no run (as `capture import` asserts what it was
  handed, ADR 0070).
- **each chart**: `irealb://` followed by that chart's percent-encoded text,
  byte for byte the slice of the playlist between its `===` separators. This
  is the link iReal Pro itself shares for one song, so a person can open the
  evidence in iReal. Its source claim pins the playlist in `derived_from` and
  the run that cut it (below), since a machine made it. One document per
  chart gives every claim a document of its own, and the same chart exported
  in two playlists has the same bytes, so the same Artifact.

Nothing is decoded into the source claims: titles, composers and styles stay
in the bytes. The playlist's name and order are kept only there too.

**What each chart yields**, all proposed in one batch per chart, with one
derivation run (ADR 0064) whose `used` pins the playlist and the chart
document:

| Claim | Version | Subject | Holds |
|---|---|---|---|
| `document_source` | 1 | the chart document | as above (first run over this chart only) |
| `chord_sequence` | 2 | the chart document | every chord in written order (section 2); no `section` labels and no `repeats`: the form holds them (ADR 0083's rule) |
| `form` | 1 | the chart document | sections, repeats with endings, D.C./D.S. (below) |
| `meter_layers` | 2, untimed | the chart document | one `written` layer; a bar per written bar with `numerator`/`denominator`, `locator` `{"bar": n}` (1-based written bar number), `section`, and `chords` with `at` offsets; pins the `form` and `sequence` |
| `key_sequence` | 1 | the chart document | one region, no anchor: the chart's key field (section 2) |

Every one is about the document, not a Work: the Work is not known until a
person links it (section 4), and ADR 0084's lead sheet already accepts a
Work's lead sheet pinning untimed inputs whose subject is a document.
The **contribution** (composer credit, scope `work`) and the **lead sheet**
need a Work, so they are made at linking, not at import.

**Agent and run.** The agent is the tool, `pkg:generic/archive-documents@<v>`.
The run's `method` is `ireal-chart`, version the grammar's version (`1`); its
`parameters` Artifact is canonical JSON naming the grammar version, the cell
placement rule and the unmarked-section label (below), so every choice the
grammar makes is on the record. The chart's playback settings (its
accompaniment style, transposition and chorus count) are not read into any
claim; they stay in the bytes.

**Quiet reruns.** Content already an Artifact's is not admitted again. A
chart whose document some run of `ireal-chart` at the same version and
parameters already used yields nothing; the same chart in another playlist
yields nothing. A new grammar version is a new run whose claims stand beside
the old ones (ADR 0077: "a better grammar later makes a new claim beside the
old one"); nothing is superseded or corrected by the importer.

**Failure is per chart and loud.** A chord symbol the grammar cannot read is
a text-only reading (ADR 0077), never a guess. Structure it cannot read (an
unknown token or time signature, a repeat or ending that does not close, an
ending mark outside a repeat, a jump form v1 cannot state such as "D.C. al
2nd ending", a chart with no time signature before its first bar) yields no
claims for that chart: its document and source claim are still admitted, and
the run report lists the chart with the reason and exits non-zero. No chart
is read in part.

**Where the code lives: a new top-level `documents/`.** The concept is the
importer of an outside document, beside `capture/`, the importer of
recordings: it admits content and reads it into claims with a run. It is not
`enrich/` (which derives from Artifacts already admitted and never admits
content) and not `world/` (which never proposes). One module per document
format; iReal is the first, Guitar Pro and ChordPro would follow. Standard
library only. Like `capture/`, it reads the archive and stages content through
enrichment's adapter and checks every draft with World's standard-library
checks first; it depends on nothing else. [Boundaries](../boundaries.md)
gains `documents/` and its command on acceptance.

### 2. The chord-symbol grammar

A chart's music field is unscrambled (iReal's fixed 50-character
obfuscation) and tokenised. Each reading keeps the chart's own markup token
verbatim as `text` (`C-7`, `Bb7b9/Ab`, `W/G`, `n`, `(Db7)`), not iReal's
rendering of it.

| Markup | Meaning | Becomes |
|---|---|---|
| `[A-G][b#]?` + quality + optional `/[A-G][b#]?` | a chord | a stack: `root` the root's class; `bass` the slash bass, else the root; `tones` the quality's classes (table below) less the bass's class, ascending |
| `W/X` | invisible root: the chord before, over a new bass | the previous entry's first stack with `bass` X; text-only when there is no previous stack |
| `n` | N.C. | `no_chord` |
| `(chord)` after a chord | an alternate shown above it | a second reading of the same entry; alone in a cell, its own entry |
| `x`, `r` | repeat the previous one or two bars | written out: a repeated bar whose only chord is on its downbeat lists no chords (held, ADR 0083); otherwise each chord is a new entry copying the repeated entry's readings and text |
| `p` | a slash (beat marker) | nothing; the chord is held |
| `T44`, `T34`, `T24`, `T54`, `T64`, `T74`, `T22`, `T32`, `T58`, `T68`, `T78`, `T98`, `T12` | time signature (`T12` is 12/8) | the bar's numerator and denominator; any other `T` code fails |
| `*A`..`*D`, `*i`, `*v` | rehearsal marks | a form section labelled `A`..`D`, `i`, `v`; kind `intro` for `i`, `verse` for `v`, none for letters |
| `{` `}`, `N1`..`N3` | repeat, endings | a form repeat with endings (`times` the number of the last ending, else 2, else a `<Nx>` comment at the close); `N0` fails |
| `S`, `Q`, `<D.C./D.S. al Coda/Fine>`, `<Fine>` | segno, coda, jumps | form `jumps` (`da_capo`/`dal_segno`; `then` `coda` with the first `Q` as `coda_from` and the second as `coda`, or `fine` at the bar carrying `<Fine>`) |
| `|`, `[`, `]`, `Z`, `,`, spaces | bar lines, cells | bars and cell positions |
| `s`, `l`, `f`, `U`, `Y`, other `<text>` | small/large chords, fermata, end, spacing, comments | nothing in a claim; recoverable from the document (comments: section 3) |

**Sections.** A new form section starts at each rehearsal mark, each `{`,
each ending, after each `}`, at a segno and at a coda. A section with no mark
of its own continues the mark in force and repeats its label (ADR 0083 lets
sections share a label); an ending's label is `1.`, `2.`, `3.`. A stretch
before the first mark has no source name; the grammar labels it `unmarked`
and names that placeholder in its parameters (decision U below).

**Offsets.** A chord's `at` is its cell: in a bar of n cells, the chord in
cell k (0-based) starts k/n of the bar after the downbeat, written exactly as
a `count` of 1/`unit` notes. This rule is checked against iReal Pro's own
playback of synthetic charts before the importer ships.

**Qualities**, as semitones above the root (the stack's root is always
asserted). Extensions imply the sevenths and ninths below them by the common
jazz convention; an eleventh is left out of a thirteenth chord. Both are
stated in the grammar, not inferred per chart.

| Quality | Classes | Quality | Classes | Quality | Classes |
|---|---|---|---|---|---|
| (none) | 0 4 7 | `-` | 0 3 7 | `+` | 0 4 8 |
| `o` | 0 3 6 | `sus` | 0 5 7 | `5` | 0 7 |
| `add9` | 0 2 4 7 | `6` | 0 4 7 9 | `-6` | 0 3 7 9 |
| `69` | 0 2 4 7 9 | `-69` | 0 2 3 7 9 | `-b6` | 0 3 7 8 |
| `-#5` | 0 3 8 | `^`, `^7` | 0 4 7 11 | `-^7` | 0 3 7 11 |
| `^9` | 0 2 4 7 11 | `-^9` | 0 2 3 7 11 | `^13` | 0 2 4 7 9 11 |
| `^7#11` | 0 4 6 7 11 | `^9#11` | 0 2 4 6 7 11 | `^7#5` | 0 4 8 11 |
| `o^7` | 0 3 6 11 | `7` | 0 4 7 10 | `-7` | 0 3 7 10 |
| `h`, `h7`, `-7b5` | 0 3 6 10 | `h9` | 0 2 3 6 10 | `o7` | 0 3 6 9 |
| `-9` | 0 2 3 7 10 | `-11` | 0 2 3 5 7 10 | `9` | 0 2 4 7 10 |
| `13` | 0 2 4 7 9 10 | `7sus` | 0 5 7 10 | `9sus` | 0 2 5 7 10 |
| `13sus` | 0 2 5 7 9 10 | `7b9sus` | 0 1 5 7 10 | `7b13sus` | 0 5 7 8 10 |
| `7susadd3` | 0 4 5 7 10 | `7b9` | 0 1 4 7 10 | `7#9` | 0 3 4 7 10 |
| `7#11` | 0 4 6 7 10 | `7b5` | 0 4 6 10 | `7#5` | 0 4 8 10 |
| `7b13` | 0 4 7 8 10 | `9#11` | 0 2 4 6 7 10 | `9b5` | 0 2 4 6 10 |
| `9#5` | 0 2 4 8 10 | `13#11` | 0 2 4 6 7 9 10 | `13b9` | 0 1 4 7 9 10 |
| `13#9` | 0 3 4 7 9 10 | `7#9#5` | 0 3 4 8 10 | `7#9b5` | 0 3 4 6 10 |
| `7#9#11` | 0 3 4 6 7 10 | `7b9#11` | 0 1 4 6 7 10 | `7b9b5` | 0 1 4 6 10 |
| `7b9#5` | 0 1 4 8 10 | `7b9#9` | 0 1 3 4 7 10 | `7b9b13` | 0 1 4 7 8 10 |
| `11` | 0 2 5 7 10, `maybe` {4} | `7alt` | 0 4 10, `maybe` {1}, {3}, {6}, {8} | | |

`7alt` and `11` use ADR 0077's uncertain tones: the chart says an altered
chord or an eleventh, not which tones sound. **Unsupported, text-only:**
`2` (iReal's "add2" or "sus2" is not settled, decision Q below), any token
not in the table, and a root iReal does not write. The importer reports the
count of text-only readings per chart.

**The key field** (`C`, `Eb-`) is one key reading: `tonic` the root's class,
`mode` `major` with the major scale as `collection`, or `minor` with the
natural minor; `text` the field verbatim. Any other value is text-only. An
empty field yields no key claim.

### 3. Style, written directions and tempo

A chart's style ("Medium Swing", "Bossa Nova"), its tempo and its written
comments ("Repeat and Fade", "Solos", "Open") are directions written over
bars: a stated intention, as [ADR 0106](0106-set-lists-and-clocks.md)'s target tempo is, never a reading of
audio. No contract holds them. The smallest home is one optional field on an
untimed bar:

- **`directions`**: 1 to 8 objects, each `text` (1 to 200 characters, the
  direction verbatim; never a lyric line) and optional `tempo` in ADR 0106's
  target-tempo shape (`bpm` exact decimal text 1 to 999 with at most three
  places, optional `beat` note value). Only an untimed bar may carry it. The
  chart's style and non-zero tempo are the first bar's direction; a comment
  that is not a jump or a repeat count is a direction on the bar it sits in.

It is a contract change. If accepted before the release that installs ADR
0113's contracts, it is part of `archive.claim/meter_layers` version 3, which
is not yet installed, so no further version is spent; otherwise it is
version 4, version 3 plus this field. World's `world/form.py` checks it; a
MusicXML or LilyPond export writes it as a `<direction>` with `<words>` and
`<metronome>`. Tempo stays derived for every timed claim (ADR 0077); the
vocabulary adds that a written direction's tempo, like a target tempo, is an
intention. **Until accepted and installed, the importer writes no direction,**
and style, tempo and comments are recoverable only from the chart document
through the importer's decoder.

### 4. Linking charts to Works and people

A title is never identity (ADR 0042), and a chart is often a standard many
Works' recordings share or a different song of the same name. Linking is a
person's assertion:

1. **Candidates are private and operational.** `documents ireal candidates`
   reads an `archive export` and the charts, and writes, into a directory the
   operator names and never into an archive or this repository, one line per
   chart document: candidate Works with a score and its reasons (normalised
   title equal, composer credit agrees, an existing MusicBrainz
   correspondence agrees, ADR 0087), and candidate person Entities for each
   composer. Losing it loses nothing.
2. **The operator confirms** in a decisions file: per chart, an existing
   Work, a new Work, or none; per composer, an existing person, a new person,
   or none.
3. **`documents ireal link`** proposes, per confirmed chart, one batch:
   - a new `musical_work` Entity when asked for (its label from the chart
     title, which names it and identifies nothing);
   - the **link**: a `document_source` version 1 claim by the operator about
     the chart document, pinning the Work in `work` ("the Works it is about",
     ADR 0085); the importer's own source claim is untouched;
   - per composer, an **`archive.claim/contribution` version 1** of scope
     `work`, pinning the Work, the chart `document`, and the `contributor`
     when confirmed; `text` and `credited_as` the composer field verbatim,
     `role` composer. It is a reading of the document, so the agent is the
     importer with a run whose `used` pins the document and the operator's
     link claim;
   - the operator's **lead sheet** (ADR 0084, version 1 or, once installed, 2)
     of the Work, pinning the chart's sequence, untimed meter, form and key,
     and the credits, labelled `iReal`.

A link already asserted for the same document and Work yields nothing. A
machine never asserts a link: the source claim's `run` means a machine made
the document, not that it chose its Work, and a link by score alone would be
a title treated as identity. A later decision may add a machine link with a
run once the candidate scores are calibrated against the operator's
confirmations.

### 5. iReal export

A new row in ADR 0089's format table:

| Format | Media type | Suffix | What it renders |
|---|---|---|---|
| `ireal` | `text/plain` | `.irealb` | an `irealb://` link of one chart |

Exporter `lead-sheet-ireal` version 1 renders exactly one lead sheet revision
of a Work with no `source` (a timed lead sheet is refused: iReal has no
time). It reads the pinned untimed meter (first `written` layer) for bars and
offsets, the form for marks, repeats, endings and jumps, the sequence for
symbols, the first key region for the key field, the credits' `credited_as`
for the composer, the Work's label for the title, and options `style`,
`tempo` and `transpose` (ADR 0089's transposition; spelling by the key,
ADR 0080) until directions exist. A stack's symbol is its verbatim `text`
when that parses under the grammar to the same stack, else the table's
quality whose classes equal the stack's; repeated identical bars are written
`x`. It is a view, filed by ADR 0089's export key; only a sent file is
admitted, which needs `archive.claim/export` version 2 (version 1 plus this
format; not installed).

**Lost, and listed in the export's losses:** readings after the second (iReal
holds one alternate), weights, uncertain tones beyond `7alt` and `11`'s
shapes, voicings, stacks with no iReal quality and text-only readings
(written as a comment over an empty cell), a root that is not asserted,
melody, lyrics and underlays, every layer but the first written one, felt
layers, groupings and modulations, time signatures outside iReal's codes,
key regions after the first, labels other than iReal's marks (written as
comments), jumps with `repeats_on_return`, more than three endings, and
sections' kinds other than intro and verse. A chart read by this importer and
exported again is equal in claims to the original wherever none of these
applies; that round trip is the exporter's test.

### 6. Placing a chart on a recording, and the chord bench

**Alignment (ADR 0077's design, built here).** For a recording and a chart
linked to the same Work (through its occurrence or match), an `enrich`
command places the chart's played bars (World's bar order over the untimed
meter and form) on the recording. Inputs: the recording's machine claims
(beat-only meter layer with downbeats, chord stacks, key regions, form spans
where present) and the chart's claims. Method: beat-synchronous dynamic
programming over (pass, played bar, beat), with the played order allowed to
repeat (a performance plays the form once per chorus), free start and end
(an intro or a tag the chart lacks stays unplaced), each of the twelve
transpositions tried, and a cost from tone-set agreement with the machine's
chords and from downbeat agreement. It keeps the best transposition only
when it beats the second by a stated margin and its cost is under a stated
limit; otherwise it proposes nothing, and the chart stays a sequence. Output,
one run whose `used` pins every claim read and the stream:

- a timed `meter_layers` version 2 pinning the untimed claim in `meter`,
  bars with `placed`, beats from the machine's beats within each bar;
- a `chord_stacks` version 2 pinning the `sequence`, spans with `entry`,
  every end `held` (hold rule "until the next chart chord"); under a
  transposition the readings are transposed and carry no `text`, since the
  text is the untransposed chart's;
- a `form_spans` version 1 pinning the `form`, spans with `section`.

The method, margins and limits are the run's parameters. This is one
asserter's placement; it does not settle the open question of matching two
asserters' beat grids, and World still pairs nothing
([open questions](../open-questions.md#claims-and-provenance)).

**The chord bench.** `enrich/bench` gains a chord set kind beside `horn-v1`,
under `ARCHIVE_BENCH_ROOT` and never in the repository: per item, the
recording's digest, the chart document's digest, the reference placement and
its transposition, and how the placement was made. A reference must not be
made from the chords it scores: it is a person's placement (section downbeats
tapped) or a chord-blind run (beats, downbeats and form spans only), and the
item says which. Scores, by mir_eval over the placed stretch only:
`mir_eval.chord` (root, majmin, sevenths, tetrads, mirex; duration weighted,
the chart's held ends as interval ends; names from World's computed Harte
strings), `mir_eval.key.weighted_score` against the key region covering most
of the stretch, the time signature's numerator against the machine's
beats per bar, `mir_eval.beat.f_measure` of downbeats (person-placed
references only), and `mir_eval.segment` boundary detection at 0.5 s and 3 s
with pairwise label agreement. The gate is the horn set's: a mean dropping
by more than 0.02 fails.

## Consequences

- A playlist becomes one small document per chart and about five claims
  each, all untimed, each by a named run; a lead sheet exists only for a
  chart a person linked.
- Charts verify the machine where it is weakest (backlog: downbeat phase,
  key, chord stacks) with a reference no extractor made.
- A failed chart is visible and loses nothing: its document is kept, and a
  later grammar reads it.
- The grammar's conventions (implied extensions, natural minor, cell
  placement) are recorded per run, so a disagreement with a person is
  between two attributed claims (INV-7, INV-8).
- Charts from iReal's forums carry unknown rights; INV-21 keeps them, their
  lead sheets and their exports within the circle until a rights claim says
  otherwise.

## Alternatives considered

- **One document per playlist only.** Every claim would need a locator into
  a multi-chart file that no locator form states, and a chart moved between
  playlists would be a new document.
- **Decoding the chart into a canonical form before admitting.** The bytes
  would no longer be what iReal wrote; a better decoder could not reread
  them.
- **Charts asserted about a Work by title.** A title is not identity (ADR
  0042); the operator's link is the only step from a document to a Work.
- **Style and tempo as their own claim.** A claim with one field over one
  document duplicates the subject and references the meter claim already
  has, and loses which bar a direction sits on.
- **Style and tempo in the lead sheet.** A lead sheet asserts no reading of
  the music (ADR 0084); a direction written on the chart is the chart's.
- **A partial reading of a chart whose structure fails.** A sequence without
  its form looks complete and is not; one report line is cheaper than a
  claim a reader must know to distrust.
- **The importer in `enrich/`.** Enrichment never admits content; this
  importer does, as `capture/` does.

## New nouns

On acceptance: *chart* (an iReal chart document), *grammar* (an importer's
stated reading of a source's symbols, versioned as its run's method),
*direction* (written text and tempo over an untimed bar), *link candidate*,
and *chord bench*. None is a subsystem; `documents/` is a home for importers,
like `capture/`.

## Decisions for the operator

- **A.** The importer and its claims as in section 1, in `documents/`.
- **G.** The grammar and quality table in section 2, including the implied
  extensions, `7alt` and `11` as uncertain tones, and natural minor.
- **Q.** Whether iReal's `2` is add2 (0 2 4 7) or sus2 (0 2 7); text-only
  until settled.
- **U.** The `unmarked` placeholder label, or a form version that lets a
  section have no label.
- **D.** `directions` on untimed bars, in meter layers version 3 or 4.
- **L.** Linking as in section 4, with no machine links.
- **E.** The `ireal` format and `archive.claim/export` version 2.
- **B.** The alignment and the chord bench as in section 6.
