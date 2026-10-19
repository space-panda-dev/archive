# 0086. Note events per part, and melody at two levels

Status: accepted (operator, 2026-10-18); amendment proposed 2026-10-19
(articulation, written rhythm, and the bass line), awaiting the operator

## Context

No claim in an archive can say which notes a player played, or what a song's
tune is. ADR 0077 holds chords as stacks and meter as a timeline, and its
"toward notated output" names what a lead sheet and a printable part still
lack: "a melody (no contract holds one)" and "note durations and rhythms,
which a later contract would add". The feature bank's `melody` extractor
(ADRs 0073 and 0074) already measures the mix's predominant melody and writes
`notes` spans, and the stems extractor (ADR 0075) separates a voice, a bass
and the rest; both stay regenerable features, never claims. A document's
track (a transcription's guitar part) has exact notes, bars and durations,
and an importer has nowhere to put them.

The operator decided the following on 2026-10-18; this record states it as
given.

- **D8.1, notes**: "one claim per part (stem or document track) per
  recording. Notes have onset/offset samples, a MIDI pitch + optional cents
  relative to the recording's tuning claim, and optional velocity.
  Document-derived notes may also carry bar/beat/duration locators. Large
  parts split across claims by time range."
- **D8.2, melody**: "at both levels: a Work-level untimed melody (the
  composition's tune), and a recording-level melody claim that selects, per
  span, which part's notes carry the tune, citing those note claims.
  Hand-offs are part changes."
- Implied, with no question needed: instrument tuning and capo per part ride
  on document-derived parts as optional fields; a mix and a part are separate
  claims, compared by a computed view labelled a difference, never a conflict
  (D3.2, [ADR 0081](0081-judgments-and-annotations.md) and
  [ADR 0082](0082-views-and-current.md)); there is no weighting across claims
  (ADR 0005, INV-8).

The product these serve is learning songs as lead sheets, showing and
correcting machine output, authoring lead sheets for the operator's own
songs, and sharing them with bandmates
([ADR 0084](0084-lead-sheets.md), [ADR 0088](0088-bandmates.md)).

## Decision

**Three contracts, in the pattern of ADR 0077.** A timed claim describes one
recording's stream in integer samples; an untimed one describes a Work or a
document and pins no audio. The step between them is a claim of its own,
never implied (ADR 0078's scopes).

| Contract | Version | Subject | Holds |
|---|---|---|---|
| `archive.claim/notes` | 1 | one recording's `source` and `stream` | one part's notes at sample spans, over the whole stream or a range |
| `archive.claim/melody` | 1 | a Work, a document, or both | the composition's tune: one line of pitches and rests as written lengths, no samples |
| `archive.claim/melody_spans` | 1 | one recording's `source` and `stream` | which notes claim carries the tune over each span |

### `archive.claim/notes` version 1: one part of one recording

A notes claim is one asserter's notes of one part. It pins what a chord
stacks claim pins for its timeline, each an exact revision:

- exactly one `source` Artifact revision, the recording whose samples it
  counts, which is the mix; a note's samples are always the mix's, even when
  the asserter listened to a stem;
- exactly one `stream`, an `archive.claim/audio_stream` version 1 Claim
  revision about that same Artifact revision; the payload's `sample_rate`
  equals the stream's, and no note ends past its `sample_count`;
- at most one `run`, an `archive.claim/derivation_run` version 1 Claim
  revision, which a machine's claim pins (ADR 0064);
- at most one `document`, the Artifact revision of the transcription or score
  whose track the part was read from;
- at most one `analysed` Artifact revision: a stem a person chose to admit as
  an Artifact (D1.1), analysed in place of the mix. A stem that stays a bank
  file is cited by bank key instead (below); a claim does one or neither,
  never both;
- at most one `tuning`, an `archive.claim/tuning` version 1 Claim revision
  about the same `source` ([ADR 0080](0080-key-and-tuning.md)): the grid its
  notes' cents are relative to;
- zero to eight `corrects`, the notes version 1 Claim revisions it corrects;
- zero to eight `derived_from`, the claims it was read from, in the role
  [ADR 0079](0079-machine-harmony-claims.md) gives chord, meter and note
  claims;
- at most one `contribution`, an `archive.claim/contribution` version 1 Claim
  revision saying whose part this is, as ADR 0078 lets a chord stacks claim
  pin one.

Its payload has `sample_rate`, `part`, `notes`, and optional `stem`, `range`,
`unpitched`, `confidence_scale`, `strings`, `capo`, `alignment`, and
`document_url`:

- `part`, required, 1 to 100 characters: the stem or document track the
  notes are of, such as `vocals` or `guitar 2`. A notes claim is always one
  part; a claim with no part (the whole sound) is what a chord stacks claim
  is for, not this one.
- `stem`, the stem the notes were read from, cited by bank key exactly as
  ADR 0079 defines it (D1.1): the extractor that made it, that bank entry's
  key, the output's name, and the stem file's SHA-256, each digest written
  `sha256:<hex>`. The stem stays a regenerable bank file; nothing is
  admitted. World checks it, and `derived_from`, with ADR 0079's one check.
- `range`, `start_sample` and `end_sample`: the stretch of the stream this
  claim covers. Absent, it covers the whole stream. Every note's onset lies
  in the range (end exclusive); its offset may ring past the end, never past
  the stream. **Inside the range, a time with no note is the part asserted
  silent; outside it, the claim says nothing.** A large part is split into
  claims by range, a note belonging to the claim its onset falls in. One
  asserter's claims of one part are meant not to overlap; that join is
  World's and no single claim can check it.
- `notes`: 1 to 100,000 objects of `onset_sample`, `offset_sample`, `pitch`,
  and optional `cents`, `velocity`, `confidence`, and `locator`, ordered by
  onset and then pitch, with no two at the same onset and pitch. Notes may
  overlap: a part may play several at once.
  - `pitch` is a MIDI note number, 0 to 127, the nearest step of the grid in
    force at its onset (ADR 0080: a drift span's grid within it, the
    reference elsewhere). So a band a quarter-tone flat still plays 60 for
    its C, and every pitch class of this record agrees with a chord stack's.
  - `cents`, exact decimal text, at least -50 and less than 50: how far the
    note sits from that step. A claim gives `cents` only when it pins a
    `tuning`, because an offset relative to an unnamed grid says nothing.
  - `velocity`, 1 to 127, as MIDI states it (0 means a note-off there, so it
    is not a velocity).
  - `confidence`, exact decimal text, exactly when the payload states
    `confidence_scale` (1 to 200 characters saying what it means), compared
    only within this claim (INV-8).
  - `locator`, only when a `document` is pinned: the document's `bar` (a
    positive integer), and optionally `at`, the onset's offset from that
    bar's downbeat, and `duration`, the written length, each a written
    length (below). D8.1's "beat" is `at`: a note falls between beats as
    often as on one.
- `unpitched`: `true` for a part whose `pitch` is a General MIDI percussion
  key (a drum track), not a pitch. Such a claim has no `cents` and pins no
  `tuning`.
- `strings` and `capo`, the **instrument fields**, only when a `document` is
  pinned: what the document says of the instrument. `strings` is 1 to 12
  open strings as MIDI note numbers in the order the instrument's tablature
  lists them, lowest staff line first, so a re-entrant tuning keeps its
  order; `capo` is the fret 1 to 24 a capo stops every string at, only with
  `strings`. They say how the part was fingered; `pitch` is still what
  sounds. A recording's own tuning is ADR 0080's claim, never this. This
  record defines the two fields once; a lead sheet's parts reuse them
  (ADR 0084).
- `alignment`, exactly when a `document` is pinned, and `document_url`, only
  then, as ADR 0077 states them.

A **written length** is `{"count": c, "unit": u}`: `c` notes of value 1/`u`
of a whole note, `u` from 1 to 1,024. A dotted quarter is `{"count": 3,
"unit": 8}` and a triplet eighth `{"count": 1, "unit": 12}`. A duration has
`c` of at least 1; an offset from a downbeat may be 0. It allows any unit,
because tuplets do not fall on powers of two, and it is the one definition:
[ADR 0083](0083-form-and-work-meter.md)'s chord offset `at` in an untimed
bar is a written length too.

```json
{
  "payload": {
    "sample_rate": 44100, "part": "vocals",
    "stem": {"extractor": "stems", "key": "sha256:3f…(64 hex)", "output": "vocals", "sha256": "sha256:9a…(64 hex)"},
    "range": {"start_sample": 0, "end_sample": 2646000},
    "confidence_scale": "voicing confidence of the melody extractor, version 2",
    "notes": [
      {"onset_sample": 44100, "offset_sample": 66150, "pitch": 67, "cents": "-8", "confidence": "0.91"},
      {"onset_sample": 66150, "offset_sample": 110250, "pitch": 69, "cents": "4", "confidence": "0.87"}
    ]
  },
  "refs": [
    {"role": "source", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "stream", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "run", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "tuning", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

A transcription's track placed on a recording is its own notes claim, with
the `document`, its `alignment`, instrument fields, and each note's bar locator:

```json
{
  "payload": {
    "sample_rate": 44100, "part": "guitar 2",
    "alignment": {"method": "manual"},
    "strings": [40, 45, 50, 55, 59, 64], "capo": 2,
    "notes": [
      {"onset_sample": 88200, "offset_sample": 99225, "pitch": 54, "velocity": 96,
       "locator": {"bar": 5, "duration": {"count": 1, "unit": 8}}},
      {"onset_sample": 99225, "offset_sample": 110250, "pitch": 57, "velocity": 80,
       "locator": {"bar": 5, "at": {"count": 1, "unit": 8}, "duration": {"count": 1, "unit": 8}}}
    ]
  },
  "refs": [
    {"role": "source", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "stream", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "document", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

**Corrections.** A correction is its own notes claim of the same part,
citing in `corrects` what it corrects, and replaces nothing (ADR 0005). Its
`range` is what it covers: a person who fixes four bars of a machine's vocal
line asserts those bars' notes with a range around them, and World's
correction overlay (ADR 0082) shows their notes
over that range, the machine's beside them, and the machine's elsewhere. A
person's single wrong-note fix is a correction with a short range, because
the range is what says "here, these and only these notes".

**A part and the mix are different things, and differ.** A vocal stem's
notes and a guitar track's notes over the same seconds are two claims about
two parts. Where one part's notes leave the tones of a chord stacks claim of
the whole sound, that is a difference worth zooming into, found by a
computed view (D3.2), never a conflict. Two asserters' notes of the same part
over the same seconds are two readings, both kept, and a person who prefers
one judges it (ADR 0081).

### `archive.claim/melody` version 1: a Work's tune

A Work-level melody is the composition's tune, as a lead sheet writes it:
one line of pitches and rests in written order, each with a written length,
and no samples. Its subject is what it describes: it pins at least one of a
`work` (an Entity revision of kind `musical_work`) and a `document` (the
Artifact revision it was read from), at most one `run` when a tool read it,
at most one `meter`, zero to eight `corrects` (melody version 1 Claim
revisions), and zero to eight `derived_from` (ADR 0079's role: a person who
transcribes the tune from a recording-level melody cites it). It pins no
audio, and has no `sample_rate` and no `alignment`.

`meter` is an untimed `archive.claim/meter_layers` version 2 Claim revision
(ADR 0083): the Work's written bars the tune
is laid on. Without one, the tune is lengths in order with no bar lines.

Its payload has `notes`, and optional `part` (1 to 100 characters, such as
`voice`), `start`, and `document_url` (only with a pinned document):

- `notes`: 1 to 10,000 items in written order, each exactly one of a
  **pitch**, `pitch` (a MIDI number, 0 to 127) and `duration`, or a
  **rest**, `rest` (`true`) and `duration`, with `duration` a written length
  as above. Each item may carry a `locator`, the document's `bar`, only when
  a document is pinned. At least one item is a pitch. The tune is one line:
  each item starts where the one before ends, so a note held over a bar line
  is one item, and the tie is a view.
- `start`, only with a pinned `meter`: the `layer` and `bar` (indices into
  the meter claim, from 0) where the first item falls, and optionally `at`,
  its offset from that bar's downbeat, for a pickup. Absent, the tune starts
  on the downbeat of the first bar of the first layer.

The tune is laid on the meter's **written** bars, in written order, never
the played order: a repeat is played by ADR 0083's view of the form, and the
tune under it repeats with it. Laid from its start, the tune fits within
that layer's bars, which World checks when it projects both.

```json
{
  "payload": {
    "part": "voice",
    "start": {"layer": 0, "bar": 0, "at": {"count": 3, "unit": 4}},
    "notes": [
      {"pitch": 67, "duration": {"count": 1, "unit": 4}},
      {"pitch": 72, "duration": {"count": 1, "unit": 4}},
      {"rest": true, "duration": {"count": 1, "unit": 8}},
      {"pitch": 71, "duration": {"count": 1, "unit": 8}},
      {"pitch": 69, "duration": {"count": 3, "unit": 4}},
      {"pitch": 67, "duration": {"count": 1, "unit": 12}},
      {"pitch": 65, "duration": {"count": 1, "unit": 12}},
      {"pitch": 64, "duration": {"count": 1, "unit": 12}}
    ]
  },
  "refs": [
    {"role": "work", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "meter", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

Here a quarter-note pickup leads into a bar of 4/4 whose dotted half is tied
into the next bar, which ends on a triplet. Two co-writers who hear the tune
differently each assert their own melody claim; a lead sheet pins the one it
chose (D4.1, ADR 0084).

### `archive.claim/melody_spans` version 1: which part carries the tune

A recording's tune is not a new set of notes: it is the notes some part
already played. A melody spans claim pins exactly one `source` and one
`stream`, as a notes claim does, at most one `run`, 1 to 16 `notes` (notes
version 1 Claim revisions about the same `source`), zero to eight `corrects`
(melody spans version 1 Claim revisions), and zero to eight `derived_from`.
Every pinned notes claim is selected by at least one span.

Its payload has `sample_rate` and `spans`: 1 to 10,000 objects of
`start_sample`, `end_sample`, `notes`, and optional `select`, ordered and not
overlapping, as ADR 0066's spans are:

- `notes` is the revision identifier of one pinned notes claim: over this
  span, that claim's notes carry the tune. The span lies within that claim's
  range; a span that would cross from one split claim into the next is two
  spans.
- `select` is `all` (also when absent), `highest`, or `lowest`: of the notes
  that start together in the selected claim, the tune is every one, the
  highest (a guitar's top line), or the lowest.

A **hand-off** is two adjacent spans (the first ending where the second
starts) that select different notes claims: the voice to the guitar for a
solo, and back. A gap between spans is no tune asserted, not a hand-off.

```json
{
  "payload": {
    "sample_rate": 44100,
    "spans": [
      {"start_sample": 0, "end_sample": 1323000, "notes": "sha256:…(vocals)"},
      {"start_sample": 1323000, "end_sample": 1764000, "notes": "sha256:…(guitar 2)", "select": "highest"},
      {"start_sample": 1764000, "end_sample": 2646000, "notes": "sha256:…(vocals)"}
    ]
  },
  "refs": [
    {"role": "source", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "stream", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "notes", "id": "urn:uuid:…", "revision_id": "sha256:…(vocals)"},
    {"role": "notes", "id": "urn:uuid:…", "revision_id": "sha256:…(guitar 2)"}
  ]
}
```

A span names a revision rather than a position in `refs`, because the order
of references is not part of a claim's meaning.

**From a recording to a Work, and back.** The two levels are never joined by
containment. A person who transcribes a recording's tune into the Work's
melody asserts a melody claim citing the melody spans claim in
`derived_from`. Placing a Work's melody on a recording note by note is an
alignment, a later matter
([open questions](../open-questions.md#claims-and-provenance)); until then
a lead sheet that wants both pins both (ADR 0084).

**How this fits the scopes of contributions
([ADR 0078](0078-contributions.md)).** A Work-level melody is the `work`
scope: it describes the composition, as its writers do. A notes claim and a
melody spans claim are the `span` scope. A notes claim's `contribution`
says whose part it is, as a chord stacks claim's does.

**Machines and people.** The bank's `melody` extractor's `notes` spans, run
on the mix or on the `vocals` stem, become a notes claim with a `run`, its
voicing confidence on its own scale, and, for a stem, the stem's bank key;
how bank output becomes proposals is ADR 0079's (D1.4). A transcription's
importer makes one notes claim per track it places, with the document, its
alignment, locators, and instrument fields; an untimed melody claim from the
same document's melody track needs no recording. A person's own song starts
as a melody claim they write themselves. Mix first (ADR 0077) holds: a
claim from a stem sits beside one from the mix and is never ranked above it
by kind.

**Views, all computed and never stored.** World serves the tune of a
recording (each span's selected notes, tagged with the claim they came
from), its hand-offs, a Work-level melody laid on its bars with ties split
at bar lines. `world/notes.py` holds all three. The difference between a
part's notes and a chord stacks claim over the same seconds is a computed
difference (D3.2), but ADR 0082's difference view compares chord claims
only; comparing notes with chords is a later version of it.

**Exchange is lossy and says so.** None is built. A notes claim maps to
JAMS `note_midi` (pitch with cents folded in, which needs the tuning claim
to be absolute) and to a Standard MIDI File track; a Work-level melody maps
to MusicXML `<note>` and LilyPond, through the exports of
[ADR 0089](0089-practice-audio-and-exports.md). JAMS keeps no velocity,
locator, instrument or part, and float seconds instead of samples; a MIDI
file keeps no cents beyond a pitch bend and no confidence; MusicXML has no
samples. Each export lists what it dropped, as ADR 0077's do.

**The rules live in World until a release installs them.**
`world/notes.py` holds every rule above as World will re-check it, and the
three views. It uses only the standard library, so an importer or extractor
can check a proposal before Door does. Door cannot admit any of the three
contracts until a specification release installs their bundles; the release
adds the Door profile rules this record needs: notes ordered by onset and
pitch, onsets within the range and offsets within the stream, cents only
beside a pinned tuning and never on an unpitched part, confidence exactly
with a scale, locators, alignment, `document_url` and instrument only with a
document, a bank stem or an `analysed` Artifact but not both, a melody's
subject, items that are each a pitch or a rest with a positive length and at
least one pitch, `start` only with a `meter`, melody spans ordered and not
overlapping, each naming a pinned notes claim and every pinned one named,
and the existing target-measure and same-subject rules for `tuning`,
`notes`, `meter`, `corrects` and `derived_from`.

**New nouns.** Notes claim, note, part, range, written length, unpitched
part, Work-level melody, melody spans, selection, and hand-off enter the
[vocabulary](../vocabulary.md). None is a subsystem: each names part of a
claim's payload or references, or a view of one. They own no verb beyond
Annotation's and Enrichment's *assert* and *derive*, enforce no new
invariant, and hold no authoritative state outside the claims Door admits.

## Consequences

- A lead sheet has a tune to pin: a Work-level melody for the written song,
  or a recording's melody spans for "the tune as played on this take", or
  both (ADR 0084).
- Learning a part works from claims: the vocal stem's notes, a transcription's
  guitar track, and a person's corrections of either are each attributed and
  each kept. A bandmate's own transcription of their part is their own notes
  claim (ADR 0088).
- The difference between what a guitar plays and the chord heard in the mix
  is computable without a new contract: a notes claim against a chord stacks
  claim over the same samples.
- A machine run on a three-hour rehearsal fits: its notes split by range,
  each claim under 100,000 notes.
- No version of an earlier contract changes, and no earlier claim needs a
  second reading. A `derived_from` and a `tuning` role are new only on these
  new contracts and on those ADRs 0079 and 0080 amend.
- Microtonal music still sits on a twelve-tone grid with cents; a part whose
  notes are far from the grid holds them as offsets up to half a step, which
  is exact but not how its tradition writes them. That stays
  [open](../open-questions.md), as ADR 0080 leaves it.
- No invariant changes. INV-7 keeps every reading, INV-8 keeps confidence in
  its own claim's terms, and INV-24 leaves the choice of a current tune to a
  stated rule.

## Rejected

- **Notes inside a chord stacks claim** (as `voicing` over spans). A voicing
  is the pitches of one chord span; a melody is a line with its own onsets
  and lengths, and most notes of a tune are not a change of chord.
- **One claim per recording holding every part.** Parts are made by
  different asserters, at different times, from different stems and
  tracks; one claim would have to be reissued whole whenever any part was
  corrected, and could not say whose part each is.
- **One claim per note.** Thousands of records per part, each repeating the
  same source, stream and run (ADR 0066 rejected the same).
- **Copying the selected notes into the recording-level melody.** The tune
  would be a second copy of notes that a correction of the part would then
  leave stale. Citing the notes claim keeps one copy, and a correction of the
  part is picked up by citing the corrected claim in a new melody spans
  revision.
- **One contract for both melody levels.** A Work's tune is untimed and
  about the composition; a recording's is spans of samples. As ADR 0077
  found for sequences and stacks, every rule would branch on the form.
- **Pitch as frequency, or as a pitch class with an octave.** A frequency
  cannot be compared with a chord stack without the tuning, and changes when
  the grid is corrected; MIDI with cents relative to a pinned tuning keeps
  the pitch class a chord view needs and the detail a singer's intonation
  needs.
- **Cents without a tuning, relative to A = 440 Hz.** Then a detuned
  recording's every note would carry the same large offset, and the pitch
  would not be the step the band played.
- **Powers of two only for written lengths**, as ADR 0083's draft had its
  `at`. A triplet could not be written; a tuplet field would be a second way to say
  what a unit of 12 says.
- **MusicXML or MIDI as the stored form.** Neither counts samples of a
  recording, neither pins provenance, and MIDI cannot say "this is the tune".

## Amendment, 2026-10-19 (proposed): articulation, written rhythm, and the bass line

Status: proposed. Drafted at the operator's request on 2026-10-19 from the
decisions they confirmed on 2026-10-18 and 2026-10-19, for specification
0.0.10, which is unreleased, so the contracts are amended in place. The
operator decides acceptance. Until they do, the text above holds.

### Context of the amendment

Read from the vocal stem, a sung phrase of short notes with silences between
them is written today as note, rest, note, rest: the notes claim keeps the
sounded spans, and a view that writes each span's length leaves the rest of
the beat to a rest. A musician writes the same phrase as dotted eighths and
sixteenths marked staccato. On one album track, the plain reading wrote 295
rests among 543 notes; reading each note to the next onset and marking the
short ones staccato wrote 20 rests, with 233 notes (43%) staccato. Nothing
in notes or melody version 1 can say staccato.

Two further faults showed in the same runs. The bass part is not a line of
its own in any view, so a lead sheet or export cannot give it a staff, and
a recording-level melody is free to select it. And a machine vocal line
carries separation bleed: low notes from the bass that leak into the vocal
stem while the singer is silent. Dropping them removed 6% to 25% of the
vocal-line notes on the tracks tried.

### Decision of the amendment

**Sounded timing and written rhythm are kept apart.** A note's
`onset_sample` and `offset_sample` remain what sounded. How it is written is
a named World view, **written rhythm**, computed and never stored:

- Onsets are placed on the beat grid of the recording's meter claim
  (ADR 0077); without one, the view writes nothing.
- A note's written length fills the interval from its onset to the next
  note's onset. When the sounded share of that interval is short (initially
  under 60%), the note is marked `staccato` instead of being followed by a
  rest.
- A rest is written only for a silence with a reason, which the view states:
  `line_end` (a timed-lyrics line ends on the note, ADR 0085), `breath` (a
  silence of at least a beat between words of a line), or `gap` (a silence
  of at least a bar anywhere).
- The thresholds are the view's parameters, named by its version, so two
  readings of one claim agree.

A person who writes it otherwise asserts a correction: a notes claim with
their own articulation, or a Work-level melody with their own rests.

**Notes carry articulation.** A note of a notes claim, and a pitch item of a
Work-level melody, may carry `articulation`: one to four distinct names
from `staccato`, `staccatissimo`, `tenuto`, `accent`, `strong-accent` (a
marcato), and `fermata`, each as MusicXML names it. It is what the document
or the person wrote, never what a machine measured; a machine's short note
is short in its samples, and the written rhythm view marks it.

**The bass line is a line of its own.** The bass part's notes (a notes claim
of the `bass` part, read from the bass stem or a document's bass track) are
served by a **bass line** view with its own written rhythm, and never merged
into a melody. A recording-level melody never selects the bass part by
default: no machine proposes a melody spans claim with a span on it, and
World's default tune of a recording skips such spans. A person may assert
one (a bass solo), and it is shown as they assert it.

**A machine vocal line drops separation bleed.** Before proposing a vocal
notes claim, the extractor drops a note that lies below the singer's range
(estimated from the same run's notes), or that matches the concurrent bass
note while the voice is unvoiced. The rule and its thresholds are the run's
parameters (ADR 0064), so what was dropped can be recomputed from the run,
and a person who wants a note back asserts it.

**Exports give the bass its own staff.** The exporters of
[ADR 0089](0089-practice-audio-and-exports.md) write the bass line on a bass
clef staff of its own, and write articulation as MusicXML `<articulations>`
(a fermata as `<fermata>` under `<notations>`) and LilyPond's marks. A rest's
reason has no MusicXML or LilyPond form; the export lists it as dropped.

### Contract changes the amendment implies

1. `archive.claim/notes` version 1: a note gains optional `articulation`.
2. `archive.claim/melody` version 1: a pitch item gains optional
   `articulation`; a rest item gains optional `reason`, one of `line_end`,
   `breath`, `gap`.
3. Door profile rules: `articulation` names known and distinct, one to four,
   never on a rest; `reason` only on a rest.
4. `world/notes.py`: the written rhythm and bass line views; the default
   tune skips spans that select the bass part; the check that a machine's
   melody spans claim selects no bass part.
5. The `melody` extractor's manifest and parameters: the bleed filter.
6. ADR 0089's MusicXML and LilyPond exporters: the bass staff and
   articulation; the export's dropped-fields list gains a rest's reason.

### Consequences of the amendment

- A sung phrase of short notes reads as a musician writes it; on the track
  above, rests fell from 295 to 20.
- Staccato can be dense: 43% of one track's notes. Whether a view should
  stop marking it past some density, and say so instead, is
  [open](../open-questions.md#claims-and-provenance).
- A bassist's part is printable on its own staff from the bass stem alone.
- The 0.0.10 bundles change before release; no released claim needs a
  second reading.
- Written rhythm, articulation, bass line, and rest reason enter the
  [vocabulary](../vocabulary.md) when the amendment is accepted.
- No invariant changes.

### Rejected in the amendment

- **Storing the written rhythm in the notes claim.** It depends on a meter
  claim and on thresholds that will be tuned; stored, every retuning would
  reissue every claim. A person's own writing is stored, as a correction.
- **Writing every silence as a rest.** It is what produced 295 rests; a
  reader cannot see the phrase for them.
- **Merging the bass into the melody, or a bass flag on melody spans.** The
  bass is a part with a line of its own, and a bass solo is already a span a
  person can assert.
- **Leaving bleed to the reader.** Bleed notes are below the singer and
  under the bass: a view cannot tell them from a low sung note without the
  run's voicing, which only the run has.
