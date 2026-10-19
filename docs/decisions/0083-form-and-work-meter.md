# 0083. Form is a claim, and a Work can have meter without a recording

Status: proposed

A lead sheet is chords in bars, in sections, with repeat signs, first and
second endings, a segno and a coda. Specification 0.0.9 can hold the chords
of a Work without times (`archive.claim/chord_sequence` version 1,
[ADR 0077](0077-chord-stacks-and-meter.md)), but not the rest:

- **No claim holds form.** A sequence entry may carry a `section` label, the
  source's own word, and a sequence's `repeats` say that a range of entries
  is played several times. There are no first or second endings, no codas,
  no D.C. or D.S., and nothing says where a recording's verse starts. The
  feature bank's `structure` extractor finds sections in the audio
  ([ADR 0074](0074-extractor-contract.md)), but as regenerable features, never
  as claims.
- **A Work has no meter.** `archive.claim/meter_layers` version 1 pins a
  `source` and a `stream` and counts every bar in samples. The operator's own
  song, written down before or without any recording, cannot say "eight bars
  of 4/4, then a bar of 7/8"; a chart's bar grid can be asserted only after
  it is placed on a recording.

The operator decided the following on 2026-10-18, in the design of
specification 0.0.10; this record states it as given.

- **D5.1.** Form is its own claim: untimed on a Work (sections, repeats, 1st
  and 2nd endings, codas, D.S.) or timed spans on a recording, linked by
  alignment like sequences. Labels are free text with an optional common
  vocabulary.
- **D5.2.** A Work may have untimed meter layers (bars, no samples), tied to
  sequence entries and form sections. Alignment to a recording yields a timed
  meter claim citing it.

The product this serves is learning songs as lead sheets, correcting what
machines heard, authoring lead sheets for the operator's own songs, and
sharing them with bandmates. A lead sheet itself is a claim that pins the
form, meter, chord, lyric and melody claims it chose (ADR 0084); this record
defines only the form and the untimed meter it pins.

## Decision

**Three contracts, in the pattern of ADR 0077.** An untimed claim describes a
Work or a document, and pins no audio. A timed claim describes one recording's
stream in integer samples, and may pin the untimed claim an alignment placed
on it. One untimed claim may be placed on many recordings, each placement its
own claim. Nothing is overwritten (ADR 0005); where a recording differs from
the written form (an extra chorus, a cut bridge), both stay, and the
difference is data.

| Contract | Version | Subject | Holds |
|---|---|---|---|
| `archive.claim/form` | 1 | a Work, a document, or both | sections in written order, repeats with endings, D.C. and D.S. jumps |
| `archive.claim/form_spans` | 1 | one recording's `source` and `stream` | sections at sample spans |
| `archive.claim/meter_layers` | 2 | either a recording (as version 1) or a Work or document | timed: version 1's bars and beats, which may name the untimed bar each was placed from; untimed: bars with no samples, in sections, holding chord sequence entries |

### `archive.claim/form` version 1: sections in written order, with no times

Its references, each pinning an exact revision:

- at most one `work`, an Entity revision of kind `musical_work`;
- at most one `document`, the Artifact revision the form was read from;
- at least one of the two: the subject is what the form describes;
- at most one `run`, an `archive.claim/derivation_run` version 1 Claim
  revision, when a tool read the document ([ADR 0064](0064-derivation-runs.md));
- zero to eight `corrects`, the `archive.claim/form` version 1 Claim
  revisions this claim corrects. A form corrects only a form.

Its payload has `sections`, and optional `repeats`, `jumps`, and
`document_url` (an http or https URL of at most 2,000 characters, only beside
a pinned document, as ADR 0077 states it). It has no `sample_rate` and no
`alignment`.

```json
{
  "sections": [
    {"label": "Intro", "kind": "intro"},
    {"label": "A", "kind": "verse"},
    {"label": "B", "kind": "chorus"},
    {"label": "1."},
    {"label": "2."},
    {"label": "Bridge", "kind": "bridge"},
    {"label": "Coda", "kind": "coda"}
  ],
  "repeats": [
    {"first": 1, "last": 4, "times": 2,
     "endings": [{"first": 3, "last": 3, "passes": [1]},
                 {"first": 4, "last": 4, "passes": [2]}]}
  ],
  "jumps": [
    {"after": 5, "kind": "dal_segno", "to": 1, "then": "coda",
     "coda_from": 4, "coda": 6}
  ]
}
```

- **`sections`**: 1 to 1,000 objects in the source's written order, each a
  stretch of the music between two structural marks. A section has `label`,
  1 to 100 characters, the source's own name for it, verbatim ("Verse 2",
  "A", "Solo (keys)", "1."); an optional `kind` from the common vocabulary
  below; and, when a document is pinned, an optional `locator`: exactly one
  of the document's `line` or `bar`, a positive integer. Located sections all
  use the same kind of locator and never go backwards. A section's label is a
  structural name, never a quoted lyric line ("lyrics stay out",
  ADR 0077). Two sections may share a label and a kind: a second verse is
  another section, written again.
- **`kind`**, the optional common vocabulary: `intro`, `verse`,
  `pre_chorus`, `chorus`, `post_chorus`, `refrain`, `bridge`, `interlude`,
  `instrumental`, `solo`, `break`, `vamp`, `head`, `outro`, `coda`, `tag`. A
  kind is what a reader may filter or colour by; the label is always kept,
  and a section whose name fits no kind simply has none.
- **`repeats`**: exactly a chord sequence's repeats (ADR 0077), applied to
  sections: each `first` and `last` section index and `times` (2 to 999),
  ordered by first section with the outer of two ranges first; two ranges
  nest or are apart, never cross. The one addition is optional `endings`.
- **`endings`**: 2 to 16 voltas at the end of a repeat. Each has `first` and
  `last` section and `passes`, the distinct ascending pass numbers (1 to the
  repeat's `times`) that take it. The first ending starts after the repeat's
  first section, so the body is never empty; each ending starts where the one
  before it ends; the last ends at the repeat's `last`; and every pass takes
  exactly one ending ("1.–3." is one ending of passes 1, 2 and 3). The body
  is the sections before the first ending. A repeat inside one with endings
  lies wholly in its body or wholly in one ending.
- **`jumps`**: 0 to 8 D.C. and D.S. signs, at distinct sections in written
  order. A jump is at the end of section `after`. Its `kind` is `da_capo`
  (back to section 0) or `dal_segno` (back to the segno at the start of
  section `to`, at most `after`). Its `then` says how the return ends: `end`
  (play on to the last section), `fine` (stop at the end of section `fine`,
  at or after where the return starts), or `coda` (at the end of section
  `coda_from`, at or after where the return starts, go to section `coda`,
  which is after both the jump and `coda_from`, and play on to the end).
  Optional `repeats_on_return`, true or false, says whether repeats are
  taken again on the return; absent, they are not, and each repeat is played
  once with the ending of its last pass, as the common convention reads. A
  jump never lands strictly inside a repeat: a segno or a coda is at a
  repeat's first section or outside every repeat.

**The played order is a view.** World computes the sections' played order
from a checked form, and never stores it: every repeat unrolled, each pass
taking its ending, and each jump taken once, the first time its section ends
on the last pass of every repeat around it. The example plays Intro, A, B,
1., A, B, 2., Bridge, then from the segno A, B, 2., and the coda: sections 0,
1, 2, 3, 1, 2, 4, 5, 1, 2, 4, 6. A source whose signs mean something else
(a D.C. that takes its repeats, a coda that is not at the end) states that
in its own fields or is written out as sections in played order; a form is
never forced into a convention its source did not follow.

**Repeats are defined once.** The repeat shape and its rules are chord
sequence version 1's, not a second definition; this contract only adds
`endings` and jumps, which a chord sequence does not have. What each claim's
repeats are about differs, so an importer never writes one repeat sign
twice:

- A **form's** repeats are over sections: the repeat signs, voltas and
  navigation of the source's structure.
- A **chord sequence's** repeats are over chords within the source's written
  lines: a cue sheet's "x4" over two chords, when no form says more.
- When an untimed meter claim ties a sequence's entries to bars and those
  bars to a form's sections (below), the played order is the form's; the
  sequence's own repeats are not applied again. A chart with repeat signs
  around sections is imported with those signs in the form, and its
  sequence has none for them; an "x4" inside a section stays in the
  sequence or, when the meter gives it bars, becomes a section of its own
  with a repeat in the form.

### `archive.claim/form_spans` version 1: where a recording's sections are

A timed claim with ADR 0077's references: exactly one `source` Artifact
revision (the mix) and one `stream` (`archive.claim/audio_stream` version 1
about that revision), at most one `run`, at most one `document`, zero to eight
`analysed` Artifact revisions; and, new here, at most one `form`, the
`archive.claim/form` version 1 Claim revision an alignment placed on this
recording, and zero to eight `corrects`, the `archive.claim/form_spans`
version 1 Claim revisions it corrects. A timed form corrects only a timed
form.

```json
{
  "sample_rate": 48000,
  "alignment": {"method": "manual"},
  "spans": [
    {"start_sample": 0, "end_sample": 384000, "section": 0},
    {"start_sample": 384000, "end_sample": 1152000, "section": 1},
    {"start_sample": 1152000, "end_sample": 1536000, "section": 2,
     "label": "Chorus, double time"}
  ]
}
```

Its payload has `sample_rate`, equal to the stream's, `spans`, and the
alignment fields of ADR 0077: `alignment` (a `method` of 1 to 200
characters, `manual` for a person's placement, and optional
`max_error_samples`) exactly when the claim pins a `document` or a `form`,
and `document_url` only beside a document. `spans` holds 1 to 10,000 objects
of `start_sample` and `end_sample`, ordered and not overlapping, ending at or
before the stream's `sample_count`, as ADR 0066's spans are; a gap asserts
nothing. Each span has at least one of `label` (as a section's) and
`section`, the index of the pinned form's section it was placed from; a
section played three times is placed three times. It may carry `kind`, and
a `locator` (one of `line` or `bar`) when a document is pinned.

A machine segmentation asserts labels ("A", "B") and no form; a person
marking where the verses start asserts labels or kinds; a placement of a
written form asserts sections. A recording that plays the form differently
from its played order (a third chorus) is placed as played; World can show
the difference, and no rule refuses it.

### `archive.claim/meter_layers` version 2: a Work's bars, and their placement

Version 2 changes the shape, so it is a new version; version 1 stays
admissible and unchanged, and every version 1 claim stays valid under its
own contract. It is one version shared with
[ADR 0079](0079-machine-harmony-claims.md), which adds `corrects`,
`derived_from`, and (on a timed claim) a stem cited by bank key; this record
adds what follows, and a claim may use the additions of both. A version 2
claim is **timed** when it pins a `source`, and **untimed** otherwise.

**A timed version 2 claim** is version 1's in every rule (references,
`sample_rate`, bars layers, beat-only layers, roles, modulations, locators,
alignment), with ADR 0079's additions and two more:

- at most one `meter` reference, the untimed `archive.claim/meter_layers`
  version 2 Claim revision an alignment placed on this recording. A claim
  that pins one states its `alignment`, as one that pins a document does.
- a bar may carry `placed`, `{"layer": i, "bar": j}`, the untimed claim's
  layer and bar it was placed from, only when a `meter` is pinned. A placed
  bar states the same `numerator`, `denominator` and `grouping` as the bar
  it was placed from: a placement says when a written bar sounds, not what
  its meter is. A bar played several times (inside a repeat, or on a D.S.)
  is placed once per time it is played. The timed bar keeps its own `beats`.

**An untimed version 2 claim** pins at least one of a `work` (an Entity
revision of kind `musical_work`) and a `document`, at most one `run`, at
most one `form` (an `archive.claim/form` version 1 Claim revision), at most
one `sequence` (an `archive.claim/chord_sequence` version 1 or 2 Claim
revision), and ADR 0079's zero to eight `corrects` (untimed version 2
claims) and `derived_from`. It pins no `source`, `stream` or `analysed`
audio, cites no stem, and has no `sample_rate` and no `alignment`.

```json
{
  "layers": [
    {"role": "written", "bars": [
      {"numerator": 4, "denominator": 4, "section": 1,
       "chords": [{"entry": 0}, {"entry": 1, "at": {"count": 2, "unit": 4}}]},
      {"numerator": 4, "denominator": 4, "section": 1,
       "chords": [{"entry": 2}]},
      {"numerator": 7, "denominator": 8, "grouping": [2, 2, 3], "section": 2,
       "chords": [{"entry": 3}, {"entry": 4, "at": {"count": 3, "unit": 8}}]}
    ]}
  ]
}
```

Its payload has `layers` (1 to 8), optional `modulations`, and optional
`document_url`. Every layer holds `bars`, never `beats`: a beat is a sample,
and an untimed claim has none. A layer has optional `label` and `role`
(`written` or `felt`; a `felt` layer only beside a `written` one), as in
version 1. An untimed bar has:

- `numerator`, `denominator` and optional `grouping`, under version 1's
  rules; no `start_sample`, `end_sample` or `beats`;
- optional `locator`, `{"bar": n}`, the document's own bar number, only
  when a document is pinned, never going backwards;
- optional `section`, the index of the pinned form's section the bar is in,
  only when a form is pinned. Bars are in written order, so sections never
  go backwards along a layer;
- optional `chords`, 1 to 64 objects of `entry` (the index of the pinned
  sequence's entry that changes in this bar) and optional `at`, the offset
  from the downbeat as `count` (0 or more) notes of value 1/`unit` (`unit`
  one of 1, 2, 4, 8, 16, 32, 64; absent, the downbeat). Offsets rise within
  the bar and fall before its end, and entries never go back along a layer:
  a chord held over a bar line is not listed again, and a bar with no
  `chords` continues the chord before it. A chord on the "and" of two in
  4/4 is `{"count": 3, "unit": 8}`.

A modulation joins bar `bar` of a layer to the bar before it, under version
1's kinds (`equation` and `regrouping`) and fields; untimed bars are
adjacent by position.

**The played bars are a view.** With a pinned form, World plays the bars of
each section, in written order, in the form's played order; every bar then
names its section, or the view refuses rather than drop a bar. Without a form
the bars are played as written. An alignment places that played order on a
recording as a timed claim, giving each bar `placed`. The written bars stay
untimed and untouched.

**What the untimed claims say together.** A form says which sections come in
which order; a sequence says which chords come in which order; an untimed
meter claim says how many bars each section has, what meter they are in, and
which chords change where. Together they are the musical content of a lead
sheet's chord chart; the lead sheet itself (ADR 0084) pins the exact
revisions it chose, and a melody at Work level (ADR 0086) and lyrics placed
by reference (ADR 0085) join it there. None of the three needs the others: a
form alone is a road map, a sequence alone is a chart over lyrics, and a
meter claim may pin neither.

**How this fits the scopes of contributions
([ADR 0078](0078-contributions.md)).** A form and an untimed meter claim are
the `work` scope: they describe the composition, as a Work's writers do. Form
spans and a timed meter claim are the `span` scope. The step from one to the
other is an alignment, never implied by containment.

**Machines and people.** A machine's segmentation of the mix (from the bank's
`structure` feature) is a form spans claim with a `run` and labels, and goes
through the same proposal path ADR 0079 gives machine harmony. A person's
form of their own song is an untimed form claim with their agent; a
bandmate's different form of the same Work is another claim beside it
(ADR 0088). Which form a reader sees first is a view's rule (ADR 0082), never
a property of the claims. A person who disagrees with one section of a
machine's segmentation judges or annotates that span (ADR 0081) or asserts a
correcting form spans claim that cites it in `corrects`; the corrected claim
stays.

**Exchange is lossy and says so.** World's views, when built, export a form
spans claim to JAMS `segment_open` (each span's label, or its section's
label, as the value) and a form's sections, repeats, endings and jumps to
MusicXML (`<barline>` with `<repeat>` and `<ending number="1, 2">`, and
`<sound>` with `segno`, `coda`, `tocoda`, `dacapo`, `dalsegno` and `fine`).
JAMS has no field for a section's `kind` beside its label, for repeats, or
for a placement's section index; MusicXML has no sample time. No exporter is
built by this record.

**The rules live in World until a release installs them.** `world/form.py`
holds every rule above as World will re-check it, including ADR 0079's
additions to meter layers version 2, so that the version has one check, and
the played-order views (`form_order`, `bar_order`); it uses only the
standard library and reuses `world/harmony.py`'s shared checks. Door cannot admit any of the three until a specification release
installs their bundles with the profile rules this record needs: a form's
subject; located sections in document order; repeats that nest; endings that
tile the end of their repeat and give each pass exactly one ending; jumps at
distinct sections, landing outside or at the start of a repeat, whose `then`
fixes their fields; a form span's label or section, and a section only with a
pinned form; a version 2 claim that is timed exactly when it pins a `source`;
a timed bar's `placed` only with a pinned `meter`; an untimed claim's subject,
bars without samples, sections and chord entries that never go back, and
offsets within their bar; and the existing sequential-span, target-measure
and same-subject rules applied to these arrays. Until a pinned claim's payload
is in reach of the profile, the cross-claim rules (a section or entry index
within the pinned claim, a placed bar's time signature) are World's checks.

**New nouns.** Form, section, section kind, ending, jump, segno, coda, played
order, form spans, untimed meter, placed bar. Each names part of a claim's
payload or references, or a view of one; none is a subsystem, owns a verb
beyond Annotation's and Enrichment's *assert* and *derive*, or holds state
outside the claims Door admits.

## Consequences

- The operator's own song can be written down fully before it is recorded:
  its form, its bars and their meter, and its chords in them. A lead sheet
  (ADR 0084) pins those three claims and renders without any audio.
- A recording is placed against the written song section by section and bar
  by bar, and each placement names what it placed, so a practice view can
  loop "the second ending" or "bar 3 of the bridge" on any recording that
  was placed.
- A machine's segmentation and a person's reading of where the sections fall
  coexist as claims. Where they disagree, both stay (INV-7); nothing ranks
  the person over the machine by kind (INV-8).
- Two bandmates who write a Work's form differently each assert their own
  form; neither replaces the other.
- A recording that departs from the written form (an extra chorus, a cut
  bridge, a vamp held for a solo) is placed as played. Comparing a placement
  with the form's played order is a computed difference World can show; it
  is not a conflict, and no claim is refused for it.
- Version 1 meter claims, already admitted, stay valid. An importer that
  knows only a recording keeps writing version 1 or the identical timed
  version 2 shape; only placement and untimed bars need version 2.
- No invariant changes.

## Rejected

- **Form inside the chord sequence** (sections and endings as fields of
  entries). A form exists without chords (a road map, a machine's
  segmentation), and one form serves several sequences and meter claims; a
  sequence's `section` labels stay what they are, the source's own words on
  an entry.
- **Endings and jumps added to chord sequence repeats** (a chord sequence
  version 2). The repeat signs of a song's structure would then be written
  in two claims, a sequence's and a form's, and disagree. Endings and jumps
  belong to sections; a sequence's repeats stay for repeats within a line.
- **One form contract holding both untimed sections and timed spans.** Every
  rule would branch on whether there are samples, and a Work-scoped claim
  would share a contract with a span-scoped one: ADR 0077 rejected the same
  for chord sequences.
- **An untimed meter as its own contract** (a "bar plan") rather than a
  version of meter layers. Its bars, groupings, layers, roles and
  modulations are meter layers' in every rule but time; a sibling contract
  would restate them all, and a placement would cite a claim of another
  kind than the one it produces.
- **Untimed bars as a field of the form** (sections with bar counts). A
  section's bar count says nothing about a 7/8 bar in it, its grouping, or
  where its chords change; meter belongs to the meter claim, and a form must
  be assertable without it.
- **A fixed section vocabulary instead of labels.** A source's "Verse 2",
  "Solo (keys)" or "A" would be forced into a kind it is not, and the forcing
  would be invisible afterwards. The kind is optional beside the verbatim
  label.
- **Hierarchical sections** (a verse made of phrases, an A section of two
  periods). Some sources and segmenters give levels; this record keeps one
  level and leaves nesting open.
- **Storing the played order.** It is fully determined by the sections,
  repeats, endings and jumps; storing it beside them would let the two
  disagree.
- **Chord positions as beat numbers** (`beat: 3`). A beat number cannot say
  the "and" of a beat, and in a grouped bar it is unclear whether it counts
  groups or units; an offset in note values is exact.
