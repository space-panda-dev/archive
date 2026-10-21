# 0113. Editing lead sheets: sketches, words under a Work's tune, open repeats, passages, and one save

Status: proposed (2026-10-20), its decisions settled by the operator the
same day; amends
[ADR 0083](0083-form-and-work-meter.md),
[ADR 0084](0084-lead-sheets.md),
[ADR 0085](0085-documents-lyrics-rights.md),
[ADR 0086](0086-notes-and-melody.md),
[ADR 0089](0089-practice-audio-and-exports.md) and
[ADR 0094](0094-peoples-writes.md)'s ability table

## Context

A lead-sheet editor is being built as its own application, beside the
review app and the songbook. It corrects a recorded take's chords, notes,
words and sections; writes a song from a blank page; and saves a lead sheet.
An audit of specification 0.0.10 and World (2026-10-20) found that most of
this already holds:

- **Corrections keep the machine's readings.** Chord, note, lyric-timing and
  section corrections are their own claims citing what they correct
  ([ADR 0077](0077-chord-stacks-and-meter.md), ADRs 0083 and 0086).
- **A lead sheet pins exact revisions** (ADR 0084), so later analysis never
  changes an adopted chart; the changes-since view reports what moved.
- **A recording's notes count samples, and a Work's melody counts written
  lengths** (ADR 0086), so both a take and a song with no recording can be
  written.
- **A form already holds repeats, endings, D.C., D.S., segno and coda**
  (ADR 0083), and the melody under it repeats with it.
- **Door admits a batch atomically, with an expected head per object.**
  ADR 0023's `expected_heads` names every object a batch creates or revises
  and refuses a stale one (`expected_head_stale`, a 409 at the relay); a
  batch may pin revisions admitted in the same batch
  ([ADR 0049](0049-v2-migration-route-contracts.md)) and carry staged
  content with the claims that describe it
  ([ADR 0094](0094-peoples-writes.md)).

The gaps:

- **Unfinished writing.** `check_melody` (`world/notes.py`) requires a
  `duration` on every item, so "I know these pitches, not their rhythm" has
  no form.
- **Words under a Work's tune.** Syllables name the notes of a recording
  (`carried_by`, ADR 0085's amendment); ADR 0085 left syllables under a
  Work-level melody to "the note-by-note alignment already left open".
- **Attachments when notes change.** A syllable pins a notes revision and
  its onsets; nothing says what a save that moves notes must do.
- **Open repeats.** A repeat's `times` is 2 to 999, so "vamp until cue" and
  "open for solos", common in the archive's material, cannot be written.
- **A recording against its written form.** `form_spans` names a section
  but not which pass of which repeat; a departure (an extra chorus) is only
  shown, and nothing says when a recording's chart may fold into repeat
  signs.
- **Verses under repeats.** Nothing says verse 1 is sung on pass 1 and
  verse 2 on pass 2.
- **Passages between takes.** Copying corrected notes from one take to
  another, or placing the Work's tune on a recording note by note, has no
  contract that says how precisely the two were lined up.
- **One coherent save.** The pieces exist; no test proves a save of
  corrected claims, a lyric document and a lead sheet lands whole or not at
  all.

The operator decided the following on 2026-10-20 (editor decisions E.1 to
E.26, from a grilling the same day); this record states them as given.

- **E.1** One record (this one) with amendments; the editor's own document
  model, undo and local recovery are the editor's. This record states only
  the boundary: what the editor's adapter may propose.
- **E.2** Unfinished writing is a melody version 2 whose pitched items may
  leave out `duration`: the rhythm is unknown, never guessed or padded.
- **E.3** A lead sheet pins only melodies whose every duration is known.
- **E.4** Words under a Work's melody are a new untimed claim, an
  **underlay**, naming melody items by index.
- **E.5** Attachments pin exact revisions. A save that changes notes
  re-asserts every lyric link to them, remapped; a view shows any that were
  missed.
- **E.6** A save is refused only when something it replaces has moved on.
- **E.7** One atomic admission carries a save's content and claims; band
  adoption and publication stay separate steps afterwards.
- **E.8** Saving is governed by ADR 0094's grants: `annotate` for claims,
  `admit` for a new lyric document.
- **E.9** A passage alignment says that a passage of one performance (or of
  a Work's tune) corresponds to a passage of another, at a stated precision.
- **E.10** A duration may be unknown on pitched items only; a melody with an
  unknown duration pins no `meter` and no `start`.
- **E.11** Completing one's own sketch is a new revision of it; completing
  another's is a new claim citing it in `corrects`.
- **E.12** Replaced by E.19 to E.23 (the operator: a chart and a lead sheet
  must represent repeats, "a real jazz lead sheet has a lot of go to A, and
  vamping on a solo shows up a lot in the archive material", without
  collapsing uncertain sections).
- **E.13** An underlay may pin a sketch, and a machine may propose one
  through a run.
- **E.14** A notes or melody claim is superseded by a newer revision of the
  same claim or by a correction from the same asserter; a lead sheet whose
  lyric links pin other notes or melody revisions than it shows is
  refused.
- **E.15** The precondition of E.6 names what it expects to replace; the
  editor sends nothing else.
- **E.16** Edited lyrics are a new plain-text document Artifact derived from
  the one it was edited from.
- **E.17** A lead sheet refuses a timed input copied through a `rough`
  alignment; any timed claim (chords, notes, lyric timings) may be copied
  through an alignment.
- **E.18** The Archive side is built now, in a specification release:
  melody version 2, the underlay, and the coherent save with its end-to-end
  test.
- **E.19** Open repeats: a repeat may leave out `times`, with an optional
  instruction in the source's words.
- **E.20** A recording's form spans may name the pass of each repeat they
  play; a span naming no section is a departure, kept as played.
- **E.21** A recording's chart folds into repeat signs only where its spans
  are mapped to passes by a person or at least `approximate`; passes that
  differ show as endings or variants; everything else is written out.
- **E.22** An underlay may name the repeat and passes its words go under.
- **E.23** "Repeatable" means all three: edit a written section once and
  every pass shows it; apply a recording correction to every pass mapped to
  the same section, one claim per pass; and every played order, fold and
  export is a deterministic view.
- **E.24** The first build also takes form version 2, form spans version 2
  and the fold (E.21). Passage alignment, applying a correction to every
  pass, and members' lyric documents follow.
- **E.25** ADR 0089's exporters write open repeats, last-time endings and
  stacked verses; a sketch is never exported.
- **E.26** Drafted in one pass, checked for consistency, then reviewed.

**A correction to the premise of E.6 and E.15.** The grilling assumed the
relay's stale check compared the whole archive's head. It does not: ADR
0023's `expected_heads` already holds one row per object a batch creates or
revises, and refuses only a row whose head moved. That is E.6 and E.15 as
decided, so this record adds no `expects` field and no Door change; there is
no whole-archive check to keep.

## Decision

### The adapter boundary (E.1)

The editor keeps its own document model, which may hold anything a person
is in the middle of: a half-typed chord, a note being dragged, placeholder
rests that fill a bar on screen, a passage not yet lined up. Its adapter
turns that model into proposals, and proposes only what a person
deliberately entered:

- **Never proposed:** placeholder rests, inferred ties, layout, a played
  order, a fold into repeats, a transposition, a selection, a zoom, undo
  history. Each is either a view (computed from claims) or application
  state ([ADR 0054](0054-door-service-for-applications.md)).
- **Unknown is absence.** A pitch whose rhythm is not known is proposed with
  no `duration` (below), never with a placeholder length; a bar that is not
  yet written is no items, never rests.
- **Every proposal passes World's checks first.** The checks of this record
  live in World and use only the standard library (as ADRs 0083, 0084 and
  0086's do), so the adapter runs them before it sends anything, and Door's
  refusal is a backstop, not the editor's validator.

### `archive.claim/melody` version 2: a Work's tune, which may be a sketch (E.2, E.10, E.11)

Version 2 is version 1 (ADR 0086, with its 2026-10-19 amendment) with one
change: a **pitch item may leave out `duration`**. Such an item is
**unmeasured**: its pitch and its place in the line are asserted, its
length is not known. A rest always has a `duration`, since a rest of unknown
length is no more than the gap between two notes.

A melody with at least one unmeasured item is a **sketch**:

- it pins no `meter` and has no `start`: a sketch is pitches in order, not
  yet laid in bars, and World's fit check (that the tune fits its bars)
  cannot run on it;
- its items may carry `articulation` and `locator` as in version 1;
- every rule of version 1 that does not read a duration holds (subject,
  roles, one to 10,000 items, at least one pitch).

```json
{
  "payload": {
    "part": "voice",
    "notes": [
      {"pitch": 64}, {"pitch": 67}, {"pitch": 69, "duration": {"count": 1, "unit": 2}},
      {"rest": true, "duration": {"count": 1, "unit": 4}}, {"pitch": 67}
    ]
  },
  "refs": [{"role": "work", "id": "urn:uuid:…", "revision_id": "sha256:…"}]
}
```

`corrects` and `derived_from` may pin melody version 1 or 2. Version 1 stays
installed and valid; a melody with every duration known may be asserted in
either version, and the two say the same thing.

**Finishing a sketch (E.11).** A person completing their own sketch makes a
new revision of the same claim (D-3's supersession, keeping the agent and
subject), as a set list is revised
([ADR 0106](0106-set-lists-and-clocks.md)). Completing another person's
sketch is a new melody claim of one's own that cites it in `corrects`; the
sketch stays.

**What a sketch may be pinned by.** A lead sheet never pins a sketch (E.3,
below). An underlay may (E.13). An export refuses one (E.25).

### `archive.claim/underlay` version 1: words under a Work's tune (E.4, E.13, E.22)

An **underlay** places a lyric document's syllables under the items of one
Work-level melody. It is untimed: it names melody items by index, as a
timed lyrics syllable names a recording's notes by onset (ADR 0085's
`carried_by`). ADR 0085 rejected a separate contract for that link because
the same aligner makes the timing and the link from the same stem; an
underlay has no timing to share it with, and is usually a person's.

| Role | Count | Pins |
|---|---|---|
| `melody` | exactly 1 | an `archive.claim/melody` Claim revision, version 1 or 2 (a sketch is allowed) |
| `document` | exactly 1 | the lyric document's Artifact revision, plain text, as `timed_lyrics` names it (ADR 0085) |
| `form` | 0 or 1 | an `archive.claim/form` Claim revision, version 1 or 2, only with `passes` |
| `run` | 0 or 1 | a `derivation_run` version 1 Claim revision, when a machine proposed it |
| `corrects` | 0 to 8 | `archive.claim/underlay` version 1 Claim revisions this corrects |
| `derived_from` | 0 to 8 | Claim revisions it was made from: an underlay, or a `timed_lyrics` claim whose syllables were carried over to the Work's tune |

Its payload is closed:

```json
{
  "label": "Verse 2",
  "passes": {"repeat": 0, "passes": [2]},
  "lines": [
    {"line": 5, "words": [
      {"word": 1, "items": [0]},
      {"word": 2, "syllables": [
        {"syllable": 1, "start_char": 0, "end_char": 3, "items": [1]},
        {"syllable": 2, "start_char": 3, "end_char": 7, "items": [2, 3]}
      ]}
    ]}
  ]
}
```

- `label` (optional, 1 to 200 characters): the asserter's name for this set
  of words ("Verse 2", "Spanish"). Never identity, never a quoted lyric.
- `lines` (required, 1 to 10,000): each a `line` number of the document
  (from 1, under ADR 0085's plain-text locator scheme), strictly increasing,
  with `words` (1 to 200), each a `word` number (from 1) strictly increasing
  within its line. A word holds exactly one of `items` (the whole word on
  those items) or `syllables` (1 to 32, ADR 0085's syllable fields
  `syllable`, `start_char`, `end_char`, under the same rules, with `items`
  in place of samples).
- `items`: 1 to 64 strictly increasing indices into the melody's `notes`,
  each a pitch item, never a rest. Several items are a melisma; the extender
  is drawn to the last. Across the claim, in document order, each placed
  word or syllable starts after the last item of the one before: words
  never share or cross notes. A melody item no syllable names is sung to
  nothing (an instrumental bar, a held note's tie is a view).
- `passes` (optional): `repeat`, an index into the pinned form's `repeats`,
  the innermost repeat enclosing every named item, and `passes`, 1 to 999
  distinct ascending pass numbers of that repeat. For a fixed repeat each is
  at most its `times`; for an open repeat (below) the list may end with the
  word `"last"`, or be `["last"]` alone. Given, the words are sung under
  those passes of that repeat, on every pass of any repeat around it;
  absent, under every pass the items are played. `passes` requires a pinned
  `form`, and the melody must pin an untimed meter that pins that same form
  (meter layers version 3, below, for a form version 2), so World can check
  that every named item lies in a bar of a section inside the repeat. A
  sketch has no meter, so its underlay never names passes.

**Its subject is its melody's.** An underlay pins no `work`: it is about
the Work its melody pins, and a lead sheet checks it through that melody
(below).

**Several underlays on one melody stand together.** Verse 1 on pass 1 and
verse 2 on pass 2 are two underlays naming different passes; a translation
is another underlay with its own label over the same passes. Two
asserters' underlays of the same words are two readings, both kept (INV-7).

**World checks what Door cannot see.** Door checks the shape (roles, counts,
ascending numbers, a `form` exactly with `passes`); World checks, reading
the pinned claims and the document, that every index is a pitch item of the
melody, that the line, word and character offsets fall within the document
(`check_locators`), and the pass rule above. Door does not read content.

**A machine may propose one (E.13)**, by matching a line's syllables to the
melody's phrases, through a run whose `used` pins the melody and the
document; a person corrects it with their own underlay citing it in
`corrects`. No proposer is specified by this record.

### Lyric links stay right when notes change (E.5, E.14)

A **lyric link** is a `timed_lyrics` syllable's `carried_by` (a notes revision
and its onsets) or an underlay's `melody` and `items`. Both pin exact
revisions, and neither follows the notes when they change:

- **A save that changes notes re-asserts the lyric links to them.** When
  the editor saves a new revision or a correction of a notes or melody
  claim that a lyric claim links to, it saves, in the same admission, a
  new revision of each such lyric claim (or a correction, when it is
  another person's) whose links name the new revision, remapped by the
  editor. Words never silently follow an index or an onset onto a different
  note; the editor's tests prove its remapping, and World's existing check
  that every named onset or index exists in the pinned revision is the
  backstop.
- **Superseded, defined.** A notes or melody claim revision is superseded
  when the same object has a newer revision, or when a claim by the same
  asserter cites it in `corrects`. A correction by someone else does not
  supersede it; the two coexist (INV-7). A lyric document is superseded when
  a later `document_source` claim, by the asserter of the earlier
  document's `document_source`, describes a document derived from it (E.16,
  below).
- **A view shows what was missed.** `stale_lyric_links` (World, computed,
  never stored) lists every timed lyrics or underlay revision that pins a
  superseded notes claim, melody or lyric document, with the revision that
  superseded it, unless the lyric claim is itself superseded or a later
  claim cites it in `corrects` and pins the superseding revision (or a later
  one). So a bandmate's timed lyrics, which the editor corrects rather than
  revises, leave the list once the correction is saved. An empty list is the
  editor's evidence that a save left nothing behind.
- **A lead sheet shows only words on the notes it shows (E.14).** Lead sheet
  version 2 (below) refuses a sheet whose underlay pins a melody it does not
  pin in `melody`, or whose timed lyrics name in `carried_by` a notes
  revision that none of its pinned melody spans claims select.

### Edited lyrics are a new document (E.16)

A Work's lyrics stay in a document (ADR 0085). When a person edits words in
the editor, the editor stages the new text as a plain-text document
Artifact (`/documents/stage`) and describes it in the same admission with a
`document_source` version 1 claim: `media_type` `text/plain`, `kinds`
including `lyrics`, the Work in `work`, `derived_from` the document revision
it was edited from, a `filename` and `retrieved_at` (the save's instant),
and no `url`, since nothing was retrieved. When the edited text equals an
existing document's bytes, the editor stages nothing and pins that
existing Artifact instead, since admitting it again would be refused.

A Google Doc export of the same Work stays what it was, an Artifact with its
own source claim; a later export is another candidate, never an overwrite.
Which document a lead sheet shows is the one it pins (ADR 0084); the
editor starts from that, or, with no lead sheet, from the Work's lyric
document most recently derived or retrieved, which World lists with the
chain of `derived_from` between them.

Every timed lyrics claim and underlay that placed the old document's lines
is re-asserted against the new one in the same save, under the rule above:
lines that moved are remapped by the editor, never by position.

### Open repeats (E.19): `archive.claim/form` version 2

Version 2 is version 1 (ADR 0083) with two changes to a repeat:

- **`times` may be absent: an open repeat.** It is played as many times as
  the performance plays it: a vamp until cue, choruses for solos. Each
  recording says how many (E.20); the Work does not.
- **`instruction` (optional, any repeat, 1 to 200 characters):** the
  source's own words for how it is played ("open for solos", "vamp until
  cue", "3x, last time tacet"), verbatim. Nothing parses it.

An open repeat's `endings` follow version 1's tiling rules (each starts
where the one before ends, the last ends at the repeat's `last`, the body is
never empty), with different pass rules, since the number of passes is
unknown:

- exactly one ending has `passes` `["last"]`, and names no numbers: it is
  taken on the final pass, whatever number that is;
- exactly one ending leaves out `passes`: the **default**, taken on every
  pass no ending names;
- any other ending names distinct ascending pass numbers from 1 ("1." on an
  open repeat whose second and later passes take the default);
- the default and the last-time ending may be the only two.

A fixed repeat (with `times`) keeps version 1's rules exactly, and never
uses `"last"` or a default: its passes are known.

```json
{
  "sections": [
    {"label": "Intro", "kind": "intro"},
    {"label": "A", "kind": "head"},
    {"label": "B", "kind": "head"},
    {"label": "Solos on the form", "kind": "solo"},
    {"label": "Last time"},
    {"label": "Coda", "kind": "coda"}
  ],
  "repeats": [
    {"first": 1, "last": 4, "instruction": "open for solos, head out",
     "endings": [{"first": 3, "last": 3},
                 {"first": 4, "last": 4, "passes": ["last"]}]}
  ]
}
```

**The played order of an open repeat (a view).** World's `form_order` lists
the shape of an open repeat, since the Work does not know its count:

1. each pass numbered by an ending, from 1 to the highest number any ending
   names, as its own pass (the body and the ending that pass takes, or the
   default where none names it);
2. then one pass marked `open`, the body and the default ending, standing
   for every further pass before the last;
3. then the last pass, the body and the last-time ending.

With no endings (an open vamp), it is one pass marked `open`. A recording
that plays fewer passes, or makes its first pass also its last, says so in
its form spans (E.20); the Work's view does not guess. A jump's return
takes an open repeat once, with its last-time ending, as version 1's
convention takes a fixed repeat's last pass; with `repeats_on_return: true`
it is listed again as above. Bar order (`bar_order`) follows.

Jumps, nesting, labels, kinds and locators are unchanged. `form_spans`
version 2 may pin version 1 or 2.

**`archive.claim/meter_layers` version 3** is version 2 (ADRs 0079 and 0083)
with one change: an untimed claim's `form` may pin form version 1 or 2, so a
Work's bars can be laid in the sections of a form with open repeats. Its
`corrects` and `derived_from` may pin version 2 or 3; a timed claim's `meter`
may pin an untimed version 2 or 3. Version 2 stays installed, and a claim
that needs no form version 2 may be asserted in either. Melody version 2's
`meter` and lead sheet version 2's `meter` role accept version 3.

### A recording against the written form (E.20): `archive.claim/form_spans` version 2

Version 2 is version 1 with three changes:

- `form` may pin a form version 1 or 2.
- **A span placed from a section inside repeats names its passes:**
  `passes`, one positive integer per repeat enclosing that section, outer
  first, each within its repeat's `times` (any positive integer for an open
  repeat), and optional `return: true` when the span is played on a D.C. or
  D.S. return. Only with `section`. A span placed from a section in an
  ending names a pass that takes that ending. Five solo choruses are five
  spans of the same sections with `passes` `[1]` to `[5]`.
- **`alignment.precision`** (optional): `rough`, `approximate` or `exact`,
  with the meanings of a passage alignment (below), stating what is known
  about the span boundaries whoever placed them. A person's placement
  (`method` `manual`) without one is read as `approximate`; a machine's
  without one is read as `rough`.

**A departure is a span that names no section** in a claim that pins a form
(an extra chorus, a jam, a false start). It carries its `label`, is kept as
played, and is never forced into a pass. A claim that pins no form (a
machine's segmentation) has only labels and no departures. This answers
ADR 0083's open question of how a departure is matched rather than shown:
it is recorded as one.

### When a recording's chart folds into repeats (E.21, E.23)

`folded_chart` (a named view, ADR 0082, version 1; computed, never stored)
gives a recording's chart in played order, folding runs of spans into the
written form's repeat signs only where the evidence says so:

1. **Only mapped spans fold.** Consecutive spans fold into one repeat when
   they name the same sections with consecutive passes of that repeat, in a
   form spans claim whose precision (stated, or read as above) is
   `approximate` or `exact`. A `rough` mapping never folds, whoever made it
   and whatever it says.
2. **The count is the recording's.** A folded open repeat shows the number
   of passes played; a fixed repeat played a different number of times
   shows that number, and the difference from the form is reported.
3. **Passes that differ show their difference.** For each bar position of
   the folded passes, the chord shown (by the lead sheet's precedence, or
   ADR 0082's current rule) is compared across passes. Where every pass
   agrees, it is written once. Where a pass differs, the difference is shown
   as that pass's ending when it falls where the form has endings, and
   otherwise as a variant marked with its passes ("2nd x: F7").
4. **Everything else is written out as played:** departures, unmapped
   spans, `rough` placements, and spans the form spans claim does not cover.

The view is deterministic: the same claims and the same view version give
the same chart (E.23's third meaning). Its version changes with any change
to these rules.

**Editing a written section edits every pass** (E.23's first meaning):
passes are a view of one written section, so a chord corrected in the
Work's sequence or untimed meter is shown under every pass that plays it.

### Passage alignment (E.9, E.17): `archive.claim/passage_alignment` version 1

A **passage alignment** says that a passage of one performance corresponds,
position for position, to a passage of another performance or of a Work's
tune. It is not a capture alignment (ADR 0048), which relates two sources
of one moment by their clocks, nor a document alignment, which says how a
document's positions were placed inside one claim. It is what copying a
corrected passage between takes, applying a correction to every pass, and
placing a Work's tune on a recording note by note all need.

| Role | Count | Pins |
|---|---|---|
| `from_source`, `from_stream` | both or neither | the mix Artifact and its `audio_stream` claim of the passage copied from |
| `from_melody` | 0 or 1, exactly when no `from_source` | a Work-level `archive.claim/melody` Claim revision, version 1 or 2 with every duration known |
| `to_source`, `to_stream` | exactly 1 each | the mix and stream of the passage copied to; may be the `from` recording (pass to pass) |
| `run` | 0 or 1 | a `derivation_run` version 1 Claim revision, for a machine's alignment |
| `starts_from` | 0 to 8 | `passage_alignment` revisions of lower precision this one refined from |
| `corrects` | 0 to 8 | `passage_alignment` revisions this corrects |

Its payload:

```json
{
  "precision": "approximate",
  "method": "manual",
  "max_error_samples": 4800,
  "anchors": [
    {"from": 1152000, "to": 2880000},
    {"from": 1536000, "to": 3264000}
  ]
}
```

- `precision` (required), what is known about the anchors, whoever placed
  them: **`rough`**, "about here": the passage's ends are within
  `max_error_samples` (required for `rough`), interior positions unknown.
  **`approximate`**: the first and last anchors are the passage's true start
  and end, and positions between anchors are interpolated. **`exact`**: an
  anchor at every note onset or beat of the passage. A machine that states
  `approximate` or `exact` pins its `run`, and when it refined a rougher
  alignment, pins that in `starts_from`; a person may state any precision.
- `method` (required, 1 to 200 characters): `manual` for a person, else the
  aligner's name, as ADR 0077's `alignment.method`.
- `anchors` (2 to 100,000, both sides strictly increasing): pairs of
  positions. A recording side is a sample of its stream, within its
  `sample_count`; a melody side is `{"item": i}`, the onset of melody item
  `i` (laid in written lengths from the melody's start), and an `item` may
  also give `at`, a written length into the item. Between anchors, positions
  map linearly; outside the first and last anchors nothing is said.

**Who asserts it.** A person, through `annotate` (E.8), or a machine through
its run. A `rough` alignment is often the first step: a person drags a
passage roughly into place, a machine refines it to `exact` and cites the
rough one in `starts_from`, and a person may correct either.

**Copying through an alignment (E.17, E.23's second meaning).** A timed
claim copied from one passage to another (a chord stacks, notes, timed
lyrics, form spans or timed meter layers claim) is a new claim about the
`to` recording, with its positions mapped through the alignment, citing the
claim it was copied from in `derived_from` and the alignment in a new
`aligned_by` role. Each of those contracts gains `aligned_by` (0 to 4
passage alignment revisions whose `to` recording is the claim's `source`)
in its next version, installed with passage alignment; `timed_lyrics`,
which has no `derived_from` today, gains that role in the same version. Applying a correction to every pass is one alignment per
pass (within one recording) and one copied claim per pass, each its own
correction of the machine claim there, so a pass played differently can
still be corrected on its own.

**Placing a Work's tune on a recording** is an `exact` alignment from the
melody to the recording; a notes claim derived through it is the written
tune as placed, and an underlay's words carried over become a timed lyrics
claim `derived_from` the underlay and `aligned_by` the alignment. This
answers the open question of how a Work's melody is placed on a recording
note by note.

**A lead sheet refuses rough copies (E.17).** The lead sheet version
installed with passage alignment (version 3), whose input roles accept the
versions that carry `aligned_by`, refuses a pinned timed input whose
`aligned_by` names an alignment of precision `rough`; it needs
`approximate` or `exact`. Lead sheet version 2 cannot pin such an input at
all, since those versions do not exist when it is installed.

### `archive.claim/lead_sheet` version 2 (E.3, E.14)

Version 2 is version 1 (ADR 0084) with these changes:

| Role | Change |
|---|---|
| `melody` | may pin melody version 1 or 2, `melody_spans` version 1; a melody version 2 with any unmeasured item is refused (E.3) |
| `form` | may pin form version 1 or 2, form spans version 1 or 2 |
| `meter` | may also pin meter layers version 3 |
| `underlay` | new: 0 to 8 `archive.claim/underlay` version 1 Claim revisions, only with `lyrics`; each places exactly the pinned lyric document and pins a melody revision the lead sheet pins in `melody` |
| `derived_from` | may pin lead sheet version 1 or 2 |

and one rule: a pinned timed lyrics claim's `carried_by` revisions are each
selected by a pinned melody spans claim (E.14). The order of `underlay` references
is the asserter's precedence, as for every role. Version 1 stays installed;
a version 1 lead sheet's next revision may be version 2 (same agent, Work
and source).

### One coherent save (E.6, E.7, E.15)

A save is **one admission** (ADR 0023) carrying everything a person changed,
in dependency order within the batch:

1. the new lyric document's staged content and its `document_source` claim,
   when words changed (E.16);
2. the musical claims: corrections, new revisions of the person's own
   claims, melody, form, meter;
3. the re-asserted lyric links: timed lyrics and underlays naming the new
   revisions (E.5);
4. the lead sheet's new revision, pinning all of the above by the revision
   identifiers the batch will give them (ADR 0049's same-batch pinning).

`expected_heads` names every object the batch creates (`null`) or revises
(its head when the editor read it). That is the whole precondition (E.15):
two people's corrections of the same chord never collide, because each is
its own claim, and a save is refused only when something it revises (the
person's own earlier correction, the lead sheet it supersedes, the band's
lead sheet a band member revises) has moved on since it was read.

**On refusal nothing lands, and the draft is kept.** Door refuses the batch
whole (`expected_head_stale`, or any check), writing nothing. The editor
keeps its draft, reads what changed (World's changes-since and
`stale_lyric_links`), and offers the person a rebase; it never retries a
changed batch silently. An identical retry of a committed batch returns the
stored revisions and writes nothing (ADR 0023).

**What a save never carries (E.7).** Band adoption (a band's lead sheet
with `adopted_by`, ADR 0084) and publication approvals (ADR 0010) are
separate operator steps after a save, so saving never widens reach.

**The end-to-end test.** A relay test admits one save of a staged lyric
document, its source claim, a notes correction, a re-asserted timed lyrics
claim, a melody, an underlay and a lead sheet revision pinning them; checks
World reads it back with an empty `stale_lyric_links`; then sends a second
save whose `expected_heads` is stale and checks that nothing of it was
written and the first save is intact; and checks that an identical retry
writes nothing.

### Who may save (E.8)

Under ADR 0094's grants, in member capacity, over what the caller's
audiences read: `annotate` covers melody version 2, the underlay, form
version 2, form spans version 2, passage alignment, lead sheet version 2,
and the next versions that carry `aligned_by`; `admit` covers a new lyric
document staged by the caller with its `document_source` claim in the same
batch, which ADR 0094 already decided (its `admit` row opened staging to a
member who holds it, answering ADR 0085's "a later decision"). A band's adopted lead sheet stays the operator's
step. The operator saves now; members save once ADR 0094 is built.

### Exports (E.25): ADR 0089's exporters

- **Repeats, endings and jumps** are written from the form as ADR 0083
  sketched (MusicXML `<barline>`, `<repeat>`, `<ending>`, `<sound>`;
  LilyPond `\repeat volta` with `\alternative`, `\segno`, `\coda`).
- **An open repeat** is a repeat with no `times` attribute and its
  `instruction` as a `<words>` direction (LilyPond `\mark` text); a
  last-time ending's printed text is "Last time", or the instruction's.
- **Stacked verses** are numbered lyric lines under a repeat: an underlay
  naming passes takes the number of its lowest pass (`"last"` counting as
  one more than the highest numbered pass), and its label, or its pass
  numbers ("1., 3."), is printed before it; a second underlay on a number
  already taken, in the lead sheet's order of `underlay` references, takes
  the next free number; an underlay with no passes follows them all.
  MusicXML writes `<lyric number="n">`; LilyPond a `\lyricmode` verse per
  number.
- **A sketch is never exported**: the exporter refuses a lead sheet or
  melody with an unmeasured item, which a lead sheet cannot pin anyway.
- The export's losses gain an open repeat's played count (MusicXML has no
  "open"), and a variant pass in a folded chart (written as a text
  direction).

### What this extends

- **ADR 0083**: form version 2, form spans version 2 and meter layers
  version 3 as above; the earlier versions stay installed. Its open question on departures is answered.
- **ADR 0084**: lead sheet version 2.
- **ADR 0085**: the underlay answers "syllables under a Work's melody";
  edited lyrics are documents derived from their predecessor (members
  stage them under ADR 0094's `admit`, as that record already decided).
- **ADR 0086**: melody version 2; placing a Work's melody on a recording is
  a passage alignment.
- **ADR 0089**: the exporters' additions above.
- **ADR 0094**: the `annotate` row gains this record's contracts.
- **ADR 0023**: read, not changed: `expected_heads` is the save's
  precondition.

### World

`world/notes.py` gains `check_melody` version 2 (sketch rules) and
`check_underlay`; `world/form.py` gains form and form spans version 2, meter layers
version 3,
open repeats in `form_order` and `bar_order`, and `folded_chart`;
`world/lead_sheets.py` gains version 2's roles and rules;
`world/documents.py` gains `stale_lyric_links` and the Work's lyric
documents with their derivation chain; `world/exports.py` gains open
repeats, last-time endings and stacked verses. A new `world/alignment.py`
holds `check_passage_alignment` and position mapping when passage alignment
is built. Each projects under `arch:` terms in the projection vocabulary.

## Consequences

- A song can be written from nothing: pitches first as a sketch, then
  rhythm, then bars, words under the tune, and a lead sheet, each step a
  claim and each kept.
- A jazz chart says what it says: head, open solos on the form, head out,
  a last-time ending, a D.S. al coda; each recording says how many choruses
  it played.
- A recording's chart folds into repeats only where a person or a good
  alignment said the passes are the same; a machine's guess never hides an
  extra chorus.
- A save lands whole or not at all; a stale one costs the person a rebase,
  never their draft.
- Words never land on the wrong note silently: the editor remaps, World's
  checks refuse a missing note, `stale_lyric_links` lists a missed remap,
  and a lead sheet refuses a mismatch.
- Six new contract versions (melody 2, form 2, form spans 2, meter layers
  3, lead sheet 2, underlay 1) install in the next specification release;
  passage alignment, the `aligned_by` versions and lead sheet 3 follow in a
  later one.
- No invariant changes. INV-7 keeps every reading, sketch and alignment;
  INV-8 holds because folding and precedence are stated rules, not weights.

## Alternatives considered

- **Keeping sketches in the editor only.** A phone sketch would never reach
  the tablet and would not be backed up, and "the tune goes E, G, A" is
  evidence about the Work.
- **The editor's draft file as a document.** The archive would hold an
  application's format it cannot read, and its pitches would be invisible to
  every view.
- **Placeholder lengths for unknown rhythm.** A guessed quarter note is
  indistinguishable from a written one; absence says exactly what is known.
- **A lead sheet that pins a sketch,** drawn as stemless noteheads. It would
  make the adopted chart incomplete and every exporter branch on it; the
  editor shows a sketch beside the lead sheet instead.
- **Lyric locators on melody items,** or `timed_lyrics` without samples. The
  first mixes words into the tune and allows one set of words per tune; the
  second makes one contract branch on whether it has time, as ADR 0077
  rejected for chords.
- **Stable per-note identifiers in notes claims.** Attachments would survive
  edits, but notes version 1 would change shape, and an identifier that
  survives a moved note asserts "this is the same note", which is exactly
  the judgment the remapping must make deliberately.
- **A Door precondition on objects a batch does not revise** (the `expects`
  field proposed in the grilling). ADR 0023's per-object heads already
  refuse what E.6 refuses; a further check would refuse saves that collide
  with nothing.
- **Folding a recording's chart by similarity.** Two choruses that sound
  alike are not the same passes; folding by a machine's similarity would
  hide departures.
- **Repeat structure inside the melody.** The form owns repeats (ADR 0083),
  and the melody under it repeats with it; a second home would disagree.
- **Copied notes with no alignment** (only `derived_from`). Nobody could tell
  a rough placement from a precise one, and a lead sheet could not refuse
  the rough one.

## New nouns

- **Sketch**: a melody version 2 with an unmeasured item. A state of a
  claim's payload, not a contract.
- **Underlay** (`archive.claim/underlay`): words placed under a Work's tune
  by item index. (1) Nothing else says which syllable a written note
  carries. (2) No verb; a person or a machine asserts it. (3) None. (4) A
  claim contract. (5) No. (6) A claim contract (E.4).
- **Open repeat**, **default ending**, **last-time ending**, **pass**,
  **departure**: fields and readings of form and form spans claims.
- **Passage alignment** (`archive.claim/passage_alignment`), with its
  **precision**: a correspondence between two passages. (1) Neither a
  capture alignment nor a document alignment relates two performances or a
  tune and a performance. (2) No verb; asserted. (3) None. (4) A claim
  contract. (5) No. (6) A claim contract (E.9).
- **Folded chart** and **stale lyric links**: named views. A **variant** is
  a folded pass whose chords differ where the form has no ending; a
  **lyric link** is a syllable's `carried_by` or an underlay's items.

## Touch points

Verified to exist: `world/notes.py` (`check_melody`, `MELODY_ROLES`);
`world/form.py` (`form_order`, `bar_order`); `world/lead_sheets.py`
(`check_lead_sheet`, `INPUT_CONTRACTS`); `world/documents.py`
(`_check_syllables`, `check_locators`); `world/exports.py`;
`world/contract_graphs.py` (graph handlers for each new version);
`world/circle.py` (`MEMBER_CONTRACTS` gains this record's contracts);
`relay/relay.py` and `relay/refusals.py`
(`expected_head_stale`); `relay/tests/test_relay.py` (the stale-head test
the end-to-end test extends); `door/src/Archive/Door/AdmissionPlan.hs`
(expected heads, read); `spec/archive-spec.md` and `spec/contracts/`.

New: `world/alignment.py` (with passage alignment); the version 2 and
underlay bundles in `spec/contracts/`; `docs/vocabulary.md` (*sketch*,
*underlay*, *open repeat*, *pass*, *departure*, *passage alignment*,
*folded chart*, *stale lyric links*, *variant*, *lyric link*).
