# 0107. Many devices capture one moment, MIDI is a source, and live responses wait on the node

Status: proposed (wave 2, drafted 2026-10-19)

## Context

A rehearsal is heard by several devices at once: a digital mixer writing a
multitrack over USB, a handheld recorder, a laptop, phones and a tablet
running Voice Memos, sometimes a watch, and a keyboard whose MIDI can be
recorded. [ADR 0048](0048-review-app-write-contracts.md) already lets one
`audio_selection` version 2 name every simultaneous source as a track, and
lets `capture_alignment` map one source's samples onto another's with
piecewise-linear anchors that follow clock drift. What is missing:

- **The importer makes one session per device.** `group` in
  `capture/capture.py` forms sessions per recorder grammar
  ([ADR 0070](0070-capture-import.md)), so a mixer card and a recorder card
  of the same evening become two `recording_session` Entities, against
  ADR 0070's own rule that simultaneous sources are one session. A file no
  grammar knows joins only a session named on the command line, as a new
  one.
- **Nothing proposes an alignment.** World projects `capture_alignment`
  (`_project_review_claim` in `world/world.py`), but no tool measures one
  and no view reads one, so a span on one source cannot be found on another.
- **A `session_parts` claim does not say which source it lists**
  ([open](../open-questions.md#the-archive-contract)); with one device a
  client recomputed it from the grammar, with several it cannot.
- **MIDI has nowhere to go as capture.** `audio/midi` is admissible only as a
  document through the relay's staging route
  ([ADR 0085](0085-documents-lyrics-rights.md)); a selection's spans count
  integer samples at an Artifact's own rate (S-15), and a MIDI file has no
  sample rate; a notes claim counts a recording's audio samples
  ([ADR 0086](0086-notes-and-melody.md)).
- **Practice takes vanish or float.** Practice state is application state
  ([ADR 0089](0089-practice-audio-and-exports.md)); a take someone keeps has
  no record of what it was played over, though content lineage
  ([ADR 0098](0098-content-lineage.md)) can now say so.
- **Wave 3's live runtime** (a rehearsal node, watch notifications, live
  song guess, tuning and tempo hints) is not drafted here, but the evidence
  it leaves must already have contracts.

The operator decided the following on 2026-10-19 (wave 2 decisions: the
earlier settled decisions this wave turns into contracts, and W2.13 and
W2.14); this record states them as given.

- **Multi-device capture.** Phone, watch, tablet, a digital mixer's
  multitrack over USB, the node, and a handheld recorder are each a source
  track of one moment (selection v2) joined by capture alignment; which mic
  hears talk best is a preferred-input view (ADR 0076). Device placement only
  as a free-text session note. Interim phone and tablet mics: start Voice
  Memos on each device; a native app comes with wave 3. Capture both the
  mixer multitrack and the handheld recorder (separate failure domains).
- **MIDI** recorded during a session is a capture source aligned by capture
  alignment (exact on a shared clock), not a document; MIDI is evidence for
  notes and chords. Sampler contents import as libraries in wave 2
  ([ADR 0100](0100-packs-and-collections.md)); performance lineage (which
  pad fired which sample when) waits for a later wave.
- **Live is advice, batch is evidence.** Live signals are operational and
  never admitted; the admitted recording is re-analysed in batch. Live
  analysis is Enrichment over a not-yet-admitted stream, not a new noun.
- **The rehearsal node** (a laptop now, phones and tablets later) keeps its
  own recording and a durable outbox of the operator's live responses,
  anchored to that recording's samples; after the rehearsal the recording is
  admitted, the responses go to Door, and capture alignment maps them onto
  the other sources.
- **Practice takes** are application state unless kept; a kept take is
  admitted.
- **W2.13 Live responses are ordinary claims** (titles, Work links,
  annotations) asserted by the operator, written `by` the node software, and
  anchored to the node's recording. No "live" contract.
- **W2.14 Kept practice takes** are origin `own_capture` plus lineage to the
  practice mix they were played over; "practice" is a view. No new
  `selection_kind` value.

Set lists and a clock's tempo map are [ADR 0106](0106-set-lists-and-clocks.md)'s;
voice notes are [ADR 0103](0103-voice-capture.md)'s.

## Decision

### One moment: every device is a source of one session

**A source is one device's stream of files** in ADR 0070's sense: the
handheld recorder's stereo pair, its external input, the node's recording,
one phone's Voice Memos file, and each channel the mixer writes as its own
file. A mixer that writes one polyphonic file is one source whose channels
are addressed by [ADR 0078](0078-contributions.md)'s `channel` index. Each
source has one `session_parts` claim listing its files in recording order,
and every source of one moment belongs to one `recording_session`.

**Joining a session is the operator's act, never a guess.** `capture import`
gains `--into <session object id>`: every source the import recognises (or,
with no grammar, the files named) is proposed as a new `session_parts` claim
of that existing session, beside the Artifacts, in the same content-bearing
batch. No new Entity is made, and the session keeps its identity, its name
and its primary source (the source whose first part named it, ADR 0070). An
import without `--into` behaves as today. The importer may report sessions
whose capture times overlap the new files, as a hint; it never proposes the
join itself, because file clocks on phones and recorders are wrong often
enough that a wrong join would silently merge two evenings.

**`archive.claim/session_parts` version 2** is version 1 plus a required
`source` payload object, `{"grammar": "<grammar name>", "token": "<source
token>"}` (each 1 to 100 characters; a named source without a grammar is
`{"grammar": "named", "token": "<the operator's label>"}`), so a client
reads which device and input a part list is. The primary source stays the
one that names the session; no claim flags it. The importer writes version 2
from its release on; version 1 claims stay valid.

**Device placement is a free-text note on that source.** The decision allows
placement only as free text, and the free-text claim about a recording is
[ADR 0081](0081-judgments-and-annotations.md)'s `span_annotation` of kind
`note`. A placement note is one, asserted by a person over the whole stream
of the source's first part ("tablet on the piano lid, facing the drums").
Nothing parses it.

**Both the mixer multitrack and the handheld recorder are captured.** They
are separate failure domains (two devices, two power supplies, two cards),
and the importer imports each as sources of one session. A selection may
name every track, or only those that exist for a moment, so losing one
device loses tracks, never the excerpt.

**Interim phone and tablet mics.** Each device runs Voice Memos; its files
reach the Mac by the synced folder ADR 0103 imports from. ADR 0103 imports a
voice note as its own Artifact, never forced into a session; a file becomes
a session source only by `capture import --into`. A device the operator has
marked as their own in the importer's configuration gives `own_capture`
([ADR 0096](0096-origin-of-recordings.md), R4.10); a bandmate's phone file is
admitted under the `admit` ability with `participant_capture` naming them.

### Alignment

**An aligning tool proposes capture alignments.** A new `enrich/align.py`
measures, for each part of each non-primary source, anchors against the
primary source's overlapping part, and proposes
`archive.claim/capture_alignment` version 2 pinning its derivation run
([ADR 0064](0064-derivation-runs.md)). Anchor density, the method, and
`max_error_samples` are the tool's (ADR 0048 leaves density to it). Two
sources on one clock (the mixer's channels; the node's audio and MIDI) are
aligned **exactly**: one anchor, `max_error_samples` 0 for sample-locked
audio or the stated jitter bound for MIDI, method `shared_clock`, proposed by
the recording program itself or by the importer, which knows the files came
from one clock. Competing alignments coexist; none is current (INV-7).

**`archive.view/aligned_spans` version 1** answers "where is this span of one
source in each other source of its session?". Subject: a session and one
span (`source` Artifact, `start_sample`, `end_sample`, `sample_rate`).
Answer: for each other source's part it overlaps, the mapped span (rounded
half-even, ADR 0048), the alignment revisions used, and the composed error
bound. Optional parameter `alignments`: the revisions to use. Without it the
view uses a pair's only alignment, or the operator-affirmed one among
several (`standing` in `world/judgments.py`), and otherwise lists the pair as
`ambiguous` with every candidate rather than choosing (INV-24). Mapping
through the primary source composes two alignments and adds their bounds.

### Talk: a preferred-input view

**`archive.view/talk_input` version 1** answers "which source hears the talk
in this span best?", in the spirit of [ADR 0076](0076-preferred-inputs.md):
a default input chosen by a stated rule, never stored. Subject: a session
and a span of its primary source, or an `audio_selection` revision. For each
`talk` span the session's span suggestions hold
([ADR 0091](0091-talk-needs-level.md)), it maps the span onto each source
through `aligned_spans`, and ranks the sources by how far the span's median
level rises above that source's own band level (the bank's loudness series,
`loudness-r128`), highest first, with the numbers as `why`. Parameter
`prefer`: a source the caller wants first. The transcription tool
([ADR 0104](0104-transcription-and-speakers.md)) reads its first answer as
its default input, as an extractor reads `Bank.default_input`. Sources
without the series, or without an unambiguous alignment, are listed as not
ranked, with the reason.

### MIDI is a capture source

**MIDI captured in a session enters as a source, through the importer.** A
grammar recognises the node's MIDI files and a MIDI recorder's; any other
MIDI file of a session joins by `--into`. It is admitted with a
`session_parts` claim and **no** `document_source` claim. The relay's
document route still accepts `audio/midi` for MIDI that is a document (a
transcription, a downloaded arrangement); identical bytes are one Artifact,
and each description stands.

**`archive.claim/midi_stream` version 1** gives a Standard MIDI File a
timeline, as `audio_stream` does audio ([ADR 0065](0065-audio-stream-facts.md)).
It pins exactly one `artifact` and one `run`. Payload:

```json
{"format": 1, "division": {"ticks_per_quarter": 960}, "track_count": 3,
 "event_count": 18234, "channels": [1, 2, 10],
 "sample_rate": 1000000, "sample_count": 2741532118}
```

- `format`: 0 or 1. A format 2 file holds independent sequences with no one
  timeline; no claim is proposed for it.
- `division`: `{"ticks_per_quarter": 1..32767}` or `{"smpte_fps": 24, 25,
  29 or 30, "ticks_per_frame": 1..255}`, as the header states it.
- `track_count` 1 to 65,535; `event_count` at least 0; `channels` the
  distinct channels 1 to 16 that carry channel messages, ascending.
- `sample_rate` is exactly 1,000,000: a MIDI file's samples are
  **microseconds** from its start, each event's time computed exactly from
  ticks and tempo events and rounded half-even; `sample_count` is the
  microsecond after the last end-of-track, so every event lies in
  `[0, sample_count)`. One microsecond is far below MIDI's own transmission
  time for one message, so nothing is lost.

With it, a MIDI Artifact is a selection track like any other (spans at rate
1,000,000), an `aligned` or `reference` Artifact of a capture alignment, and
a probe in `enrich/probe.py` checks it as it checks audio. Door's checks are
the payload's; like `audio_stream`, a count that cannot be made exactly is
not claimed.

**MIDI is evidence for notes and chords.** A proposer (`enrich/midi_notes.py`,
new) turns each MIDI track and channel into an `archive.claim/notes`
version 1 claim of ADR 0086 on the session's reference audio: `source` and
`stream` the audio recording's, onsets and offsets mapped through the
alignment, `pitch` and `velocity` from the note messages, `part` the track's
name or `midi channel <n>`, `unpitched` for channel 10. Its `run` lists the
MIDI Artifact, its `midi_stream` claim and the alignment revision in `used`,
so the claim names its evidence without a new reference role. No `cents`:
MIDI states steps, and a pitch bend is not read in this version. Chords are
then read from those notes as ADR 0079 reads chords from any notes claim.
A session with MIDI and no audio yields no notes claim, since ADR 0086 counts
audio samples. A clock's tempo map is ADR 0106's meter claim.

### The rehearsal node and its outbox

**The node's recording is a source.** It is written as files a node grammar
recognises (its parts, with rollover, and its MIDI), imported with
`--into` the rehearsal's session or as a new one, and is `own_capture` when
the operator marks the node as their device. It is admitted only after the
rehearsal, like any card.

**The outbox holds the operator's live responses as drafts.** It is an
append-only file beside the node's recording, written and synced to disk
before the node shows a response as taken. It is operational state of the
node (INV-4 does not apply: losing it loses responses, so it is durable, but
it is not archival until Door commits, INV-2). Each entry is one **response
group**: the drafts one gesture makes, in exactly the shape their contracts
take, except that three kinds of reference are **placeholders** the flush
resolves:

- `{"node": {"part": <n>}}` for the Artifact revision of the node's part
  `n`;
- `{"node_stream": {"part": <n>}}` for that part's `audio_stream` claim;
- `{"outbox": {"group": <seq>, "draft": <i>}}` for a draft of an earlier
  group (a title pinning the cut made a moment before).

Every response is anchored to samples of the node's recording, so every
group either drafts an `audio_selection` version 2 whose one track spans the
node's parts or pins one: a **cut** (the selection), a **title**
(`selection_title`), a **kind** (`selection_kind`), a **Work link**
(`song_occurrence` version 2 and `performance_work` version 3, pinning a
Work Entity revision the node read from World before the rehearsal, or a
new `musical_work` Entity in the same group, ADR 0048), or a **mark**
(`span_annotation` on the node's part and stream). A gesture at a point in
time becomes a span chosen by the node (wave 3's choice, such as the last
ten seconds); a draft without a span is not possible. Each draft's `agent`
is the operator's person; its `by` is the node software's Package URL
(W2.13). An entry may carry the node's wall-clock time for its own display;
it is never admitted, because a record's `at` is Door's recording time
(P-5), and the anchor already says when in the music the response was made.

**Flushing goes through the importer, in order.** After the node's files are
admitted and the enrichment chain has proposed each part's `audio_stream`
claim, `capture import` reads the outbox, resolves the placeholders, and
proposes each response group as its own batch, in sequence order, so that a
refused group (a Work since retired, a span past the stream's end) never
blocks the rest and is reported with its refusal. Resubmitting a committed
group is harmless: the node gives each draft its object identifier when it
writes the entry, a revision's identity excludes `at` (P-3), and Door
answers an identical retry with the stored revision. The import report
lists every group's outcome, and the node may delete its outbox only for
groups the report shows committed.

**Responses reach the other sources by alignment, not by copying.** Once an
alignment between the node and the session's primary source exists,
`aligned_spans` shows each response's span on every track, and the review
app may propose a new revision of the response's selection naming every
track (ADR 0048: a new source joins an excerpt by a revision of the same
selection). Claims pinning the first revision stay pinned to it (ADR 0044).

**Live is advice.** Whatever the node computes during the rehearsal (a song
guess, a tempo, a tuning hint, a set-list position) is Enrichment over a
stream not yet admitted and is never written to the outbox unless the
operator acts on it, and then only as the operator's claim. The admitted
recording is re-analysed in batch; a response and a later machine claim that
disagree both stand (INV-7).

### Kept practice takes (W2.14)

A take a person keeps is admitted as an Artifact with, in the same batch:

- an `archive.claim/origin` claim: `own_capture` asserted by the operator,
  or `participant_capture` naming the member who played, asserted by them;
- an ADR 0098 lineage claim whose `output` is the take and whose `input` is
  the recording whose practice render was playing (or the selection looped),
  with lineage spans placing the loop's `from` in that recording at the
  take's `to`, and the render's speed and pitch as `stretch` and `pitch`
  transforms.

The take's reach is therefore capped by the recording it was played over,
whether or not the render is audible in it: lineage only narrows. Nothing
is added to `selection_kind`, and no session is required.

**`archive.view/practice_takes` version 1** is "practice": Artifacts with an
explicit capture origin (`own_capture` or `participant_capture`) and a
lineage claim not set aside by the operator whose input is a recording or a
selection. Subject: a recording, a Work (through the occurrence chain to
recordings, ADR 0044), or a person (`captured_by`, or the operator for
`own_capture`). Answer: each take, the inputs and spans it was played over,
and its origin.

### World and the relay

- **Projection.** World projects `session_parts` version 2's `source`,
  `midi_stream` as it projects `audio_stream`, and nothing new for live
  responses or takes, which are existing claims.
- **Views.** `VIEWS` gains `aligned_spans`, `talk_input`, and
  `practice_takes`; each is regenerable and reads only admitted records.
- **Relay.** It serves the three views and every track's media as today. It
  gains no route in wave 2: the node flushes through the importer on the
  archive machine, in operator capacity. A take or a phone file a member
  admits goes through ADR 0094's streamed staging, which accepts audio.

### Which abilities apply (ADR 0094)

| Write | Operator capacity (tailnet) | Member capacity (internet, ADR 0092) |
|---|---|---|
| a session joined (`session_parts`), a `recording_session` | the operator | never: Entities and parts stay the operator's |
| `capture_alignment`, `midi_stream`, MIDI notes | machine runs, operator's tools | never: machine provenance |
| a placement note, a title, a kind, a mark, `performance_work` | yes | `annotate` |
| a cut (`audio_selection`), a take's lineage | yes | `cut` |
| a `song_occurrence` (a Work link) | yes | not in `MEMBER_CONTRACTS` today (Open, 4) |
| a phone file, a recording, a kept take | importer, or ADR 0103's route | `admit`, with its origin in the same proposal |
| origin `own_capture` | the operator only | never (ADR 0096, R4.4) |

### What wave 3 needs from these contracts

The node writes files its grammar names, an outbox of response groups with
the three placeholders, and, for its own audio and MIDI, a `shared_clock`
alignment. It reads Works and the set list from World before a rehearsal, to
pin. Phones and tablets as nodes over the internet act in member capacity:
`cut` and `annotate` for responses, `admit` for their recording, and the
answers to Open 4 and 5. Watch notifications, live song guess and hints are
advice and need no contract.

## Consequences

- One rehearsal is one session however many devices heard it, and a cut
  names every track once. The cost is a manual `--into` per extra device
  until capture time is evidence (Open, 1).
- A span found on any source can be found on all, with an error bound, and
  the transcription tool reads talk from the mic that hears it.
- Silent mixer channels with identical bytes are one Artifact; Door refuses
  two tracks with one digest (ADR 0048), so the importer lists such a file
  once and the second channel has no source of its own.
- MIDI adds exact notes for keyboard parts, on the audio's timeline, with
  their evidence named by the run.
- Live responses survive a crash of the node, enter the archive as the
  operator's ordinary claims, and never arrive before their recording.
- A kept take reaches no further than what it was played over.
- Selection version 2 may pin only `capture_alignment` version 1 (S-15's
  multi-source rule), while the aligner proposes version 2, which pins a
  run; until a selection version accepts version 2 (Open, 3), selections do
  not pin the alignments they rely on.
- No invariant changes.

## Alternatives considered

- **Join sessions by overlapping capture times automatically.** Device
  clocks drift and are set wrongly; a silent wrong join is worse than a
  manual one.
- **One session per device, grouped by a claim.** Every fact would name
  several sessions; ADR 0070 already says simultaneous sources are one.
- **A "live response" contract.** W2.13 rules it out; ordinary claims let
  every view read responses without knowing where they were made.
- **MIDI as a document.** The decision says source; a document's timing is
  bars and beats, not the session's clock.
- **Ticks as MIDI samples, or `audio_stream` for MIDI.** A tick's length
  changes with every tempo event, so it is not a rate; `audio_stream`'s codec
  and frame count mean audio.
- **A placement field on `session_parts`.** The decision says free text
  only; a structured field invites positions nobody measures.
- **A `practice` selection kind.** W2.14 rules it out.

## New nouns

- **MIDI stream** (`archive.claim/midi_stream`). (1) It gives a MIDI file a
  sample timeline and checkable bounds, which nothing else does. (2) No
  verb; a probe asserts it. (3) It lets S-15's integer spans and ADR 0048's
  alignment hold for MIDI. (4) It is code inside an existing concept: a
  claim contract beside `audio_stream`. (5) No; facts are claims. (6) A
  claim contract.
- **Rehearsal node.** (1) It records at a rehearsal and holds the operator's
  responses until its recording is admitted. (2) None in Archive; it
  records and drafts. (3) None itself; it keeps INV-2 by proposing nothing
  live. (4) It is not Archive code at all: it is a client program, as the
  review app is, and its files enter through the importer. (5) No; its
  outbox is operational. (6) An application (wave 3), not a subsystem.
- **Outbox** and **response group.** (1) A durable queue of drafts anchored
  to a recording not yet admitted. (2) None; the importer flushes it.
  (3) None; it preserves INV-2 and P-5 by holding drafts, not records.
  (4) It is a file format between the node and `capture/`. (5) No.
  (6) A protocol detail. **Placeholder** is a field of it.
- **Live response.** A name for an ordinary claim drafted at the node
  (W2.13); no contract, verb, invariant or state of its own.
- **Practice take.** A name for an Artifact with a capture origin and
  lineage to what it was played over; "practice" is the
  `practice_takes` view (W2.14). No contract, verb or state.
- **`aligned_spans`, `talk_input`, `practice_takes`.** Views (ADR 0082):
  each answers one question from admitted records, owns no state, and is
  regenerable (INV-4).
- **Source** keeps ADR 0070's meaning (one device's stream of files of a
  session); the vocabulary's *Source* (where content came from before
  admission) is a different word, and the vocabulary should say so when
  this record is accepted.

## Touch points

- `capture/capture.py`: `group` and `import_directory` (`--into`), `plan`
  (`session_parts` version 2, `source`), `chain` (the outbox flush after the
  stream probe), `main`.
- `capture/grammar.py`: `GRAMMARS` gains a node grammar, a mixer multitrack
  grammar, and a MIDI recorder grammar (new classes beside `ZoomH4E`).
- `capture/outbox.py` (new): reading entries and resolving placeholders.
- `enrich/probe.py`: `probe` gains the MIDI timeline for `midi_stream`.
- `enrich/align.py` (new) and `enrich/midi_notes.py` (new).
- `world/world.py`: `_project_review_claim` (`session_parts` version 2,
  `midi_stream`).
- `world/views.py` or `world/record_views.py`: `VIEWS` gains the three
  views; `world/judgments.py` `standing` is read by `aligned_spans`.
- `spec/archive-spec.md` and `spec/contracts/`: `session_parts` version 2
  and `midi_stream` version 1 with their profile rules;
  `docs/vocabulary.md`: *MIDI stream*, *rehearsal node*, *outbox*, *live
  response*, *practice take*, and the two senses of *source*.

## Open for the operator

1. **Capture start as evidence.** ADR 0095 expects a session date claim
   "alongside wave 2's capture work", and joining devices needs capture
   times. *Recommend:* a machine claim of an Artifact's capture start (from
   the grammar or the file's header, `unknown` when absent), read by the
   session date and by the importer's join hint; still no automatic join.
2. **The exact practice mix in lineage.** ADR 0098's inputs are Artifacts,
   selections and single stem citations; a practice render (several stems
   at chosen gains) cannot be named, so W2.14's "lineage to the practice
   mix" lands on the whole recording. *Recommend:* before ADR 0098's
   contract is installed, let an `inputs` entry be `{"practice": <practice
   request>}` (ADR 0089's request, its `content` the input's digest), as
   `recipe` already allows.
3. **Selections and alignment version 2.** Selection version 2 may pin only
   alignment version 1. *Recommend:* [ADR 0099](0099-samples.md)'s selection version 3 accepts
   alignment version 2 pins.
4. **Work links from member capacity.** `song_occurrence` is not under
   `annotate`, so a phone node over the internet cannot link a rendition to
   a Work. *Recommend:* add `song_occurrence` version 2 to `annotate`.
5. **A kept take admitted over the internet.** The operator's internet login
   is member capacity and may not assert `own_capture` (ADR 0096, R4.4), so
   W2.14 cannot be met in one proposal there. *Recommend:* admit the take
   with its lineage and origin `unknown` (ADR 0096 requires an origin in a
   member's admission; `unknown` only narrows), and have
   the practice app queue an `own_capture` claim for the operator to send
   over the tailnet; do not loosen R4.4.
6. **Placement note target.** A span annotation sits on one source's
   recording, not the session. *Recommend:* keep it there (placement is per
   device); add a session-level note only if one is needed for something
   that is not placement.
7. **MIDI pitch bend.** *Recommend:* ignore it in the first proposer; add
   `cents` from bends only with a pinned tuning, in a later version.
