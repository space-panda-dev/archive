# 0103. Voice notes arrive from phones, browsers and recorders, each its own Artifact

Status: proposed (wave 2, drafted 2026-10-19; open questions settled 2026-10-19)

The operator talks to the archive more than they write to it: an idea for a
bridge sung into a phone on a walk, "the second chorus dragged" said into the
handheld recorder between songs, a friend's voice message about a part. None
of it has a way in today.

- **The only importer is for sessions.** `capture import`
  ([ADR 0070](0070-capture-import.md)) groups every file it recognises into a
  `recording_session` with one `session_parts` claim per source, and refuses a
  file no recorder grammar recognises unless the operator names a session for
  it. A thirty-second phone memo would become a session of its own, beside
  the rehearsals.
- **Nothing says what a sessionless recording is.** An Artifact's payload is
  its content alone (D-8: no file name, capture time or media type). A
  recording outside any session has no claim that says it is a spoken note
  rather than a sample, a deposited album track or a bounce, nor when it was
  spoken.
- **Members cannot send audio yet.** [ADR 0094](0094-peoples-writes.md)
  (proposed) gives members an `admit` ability and a streamed staging route
  for audio, but no client uses it.
- **Talk inside a take is only a suggestion.** The span detector classes
  quiet spans between songs `talk` ([ADR 0066](0066-span-suggestions.md),
  [ADR 0091](0091-talk-needs-level.md)), and a person can cut one and mark
  its selection kind `talking`. Nothing tells a remark meant for later apart
  from banter.

The operator decided the following on 2026-10-19 (wave 2 decisions, the
earlier settled decisions this wave turns into contracts); this record states
them as given.

- **Voice capture.** "Apple Voice Memos (the synced folder on the Mac),
  browser push-to-talk uploaded through the relay to staging then Door (needs
  the `admit` ability), talk spans in recorder takes, friends' voice notes.
  Each voice note is its own Artifact, never forced into a session."
- **ASR runs locally only** (hosted ASR stays possible through processor
  policy, not built). Transcription is [ADR 0104](0104-transcription-and-speakers.md)'s.
- **W2.8 Speakers.** Anyone but the operator "is identified only by their own
  consent claim, made through their login"; speaker spans are ADR 0104's.
- **W2.9 Transcript text search** lives in the disposable index store
  ([ADR 0102](0102-retrieval-index.md)).
- **W2.10 Notebook links.** "A link from a note to an archive object ... is a
  claim, asserted by a person or proposed by a machine"
  ([ADR 0105](0105-notebook.md)).
- **Context (R4.10, ADR 0096).** The capture import proposes `own_capture`
  only for devices the operator configured as their own.

The operator settled this record's open questions on 2026-10-19 (round 6
answers R6.11, R6.13, R6.15 and R6.17; R6.16 settles the rest as
recommended). The decision below states the settled choices; the list at the
end records them.

## Decision

### Four routes, one claim

A **voice note** is a spoken recording kept for what it says. It arrives by
one of four routes:

| Route | What arrives | Who sends it | Becomes |
|---|---|---|---|
| `voice_memos` | a file from the operator's Voice Memos folder on the Mac | `capture voice` (below), as an operator command | a new Artifact |
| `browser` | a push-to-talk recording from a web client | the person logged in, through the relay | a new Artifact |
| `shared_file` | a file someone sent the operator (a messaging app's voice message, an e-mailed memo) | `capture voice`, as an operator command | a new Artifact |
| `within_recording` | a span of an existing recording: talk in a recorder take, or one idea in a long memo | a person, through the relay or operator tools | an `audio_selection`; no new bytes |

The first three make one Artifact per note and no session. The fourth makes
none: the take is already an Artifact in its session, and cutting its talk
into another Artifact would store the same sound twice. A
`within_recording` note becomes an Artifact only when someone renders it to
send or keep, as a regenerable render whose lineage names the selection,
and so the take ([ADR 0098](0098-content-lineage.md),
[ADR 0099](0099-samples.md)). That is the settled reading of "each its own
Artifact" (Settled 1).

**`archive.claim/voice_note` version 1** says that one Artifact or one
selection is a voice note and how it arrived. When it was spoken is not a
field here: it is the one capture-start claim of
[ADR 0107](0107-multi-device-and-live.md), `archive.claim/capture_start`,
which every reader of capture times shares (R6.15); a selection's start is
its span on an Artifact that has one. Payload:

```json
{"route": "voice_memos", "title": "bridge idea, slower", "source_id": "4F1C…"}
```

- `route` (required): one of the four above.
- `title` (optional, 1 to 200 characters): the title the device or person
  gave it.
- `source_id` (optional, 1 to 200 characters, only with `voice_memos` or
  `browser`): the sending application's own opaque identifier for the note,
  so a re-sent or edited note can be recognised (below). Never a file path,
  a device name or an account name.

References:

| Role | Count | Pins |
|---|---|---|
| `subject` | exactly 1 | an Artifact revision, or an `audio_selection` claim revision (version 2, or ADR 0099's version 3 once installed) |

Door's profile rules: `route` is `within_recording` exactly when the subject
is a selection; the envelope `agent` is a person, or software
for a note a machine read from a source's metadata (an `x-archive-agent`
rule, as for `work_origin`). The claim says nothing of who speaks (W2.8,
ADR 0104), what is said (ADR 0104's transcript), or what it is about
(W2.10's link claims, ADR 0105). Original file names and paths are the
source-path claim of [ADR 0100](0100-packs-and-collections.md) (W2.4), not a
field here.

Like any claim, voice notes coexist (INV-7). A machine's `voice_memos` claim
and a person's later one with a corrected title both stand, and a view
chooses (ADR 0082). A person who decides a file is not a voice note (a whole
rehearsal recorded on a phone, which is a capture source of
[ADR 0107](0107-multi-device-and-live.md)) judges the claim `rejected`
([ADR 0081](0081-judgments-and-annotations.md)); nothing is deleted.

### Never forced into a session

No route proposes a `recording_session` or a `session_parts` claim. A voice
note recorded at a rehearsal can be related to it later, by a W2.10 link
claim ("this note is about that session") or, when the file turns out to be
a source of the session, by the operator adding it to that session's parts
(ADR 0107). Each of those is a separate act someone chooses, never the
import's default.

### Origin

Every voice-note Artifact arrives with an origin claim
([ADR 0096](0096-origin-of-recordings.md)) in the same batch:

- **`voice_memos`**: `own_capture`, asserted under the operator's person with
  `capture voice` as `by`, because the folder is one the operator configured
  as their own device's (R4.10). The folder holds the operator's own memos;
  a memo someone else recorded on it gets a later `participant_capture`
  claim from the operator, which wins on the protective side.
- **`shared_file`**: `participant_capture` naming the sender in
  `captured_by`, asserted by the operator, who names the person on the
  command line. When the operator does not know who recorded it, `unknown`.
- **`browser`**: from a member, any value but `own_capture`, `unknown`
  allowed (ADR 0096); a member's own note is `participant_capture` naming
  themselves. From the operator over the tailnet, in operator capacity,
  `own_capture`. Over the internet the operator's login is member capacity
  (W1.3) and cannot send `own_capture`: the client sends `unknown`, and the
  review app offers the operator "mark as mine" over the tailnet, which
  asserts `own_capture` then. W1.3 and `own_capture`'s operator-only rule
  are unchanged (R6.13).

A `within_recording` note needs no origin of its own: its subject is a
selection of an Artifact that has one.

### `capture voice`

A new subcommand beside `capture import` and `capture send` imports voice
notes from a directory. It reuses the importer's steps where they fit and
drops the session:

1. **Read and hash** every file, as `read_directory` and `content_digest` do,
   and skip any whose content is already an Artifact's.
2. **Recognise** each file with a voice-source reader, the counterpart of a
   recorder grammar: it reads what the source states (a capture time, a
   title, an identifier) and never groups. The Voice Memos reader is the
   first. *To be verified before implementation:* where macOS keeps synced
   Voice Memos, the file format (expected to be MPEG-4 audio), whether titles
   and identifiers are in the file or only in the application's database,
   and what permission reading that folder needs. Nothing in this record
   depends on the answers except the reader itself.
3. **Admit** one batch per note (ADR 0023): the Artifact, its `voice_note`
   claim and its `capture_start` claim (ADR 0107, basis `grammar` or
   `header`; agent `pkg:generic/archive-capture@…`, since the importer read
   the facts), its origin claim, and a source-path claim (ADR 0100) for the
   original file name. One note per batch, so one unreadable file refuses
   only itself.
4. **Enrich** each new Artifact: `enrich probe` (audio stream facts,
   ADR 0065) and derived media for playback (ADR 0069); then local
   transcription as ADR 0104 sets it. No span detection and no work
   matching by default: a note is not a rehearsal.

`--shared --captured-by <person>` switches the route to `shared_file` and the
origin to `participant_capture`; with no reader recognising the file, its
capture-start claim has no `started`. `capture send` gains `--voice`,
streaming a folder on the Mac to the archive machine with its manifest
exactly as it streams a card, and running `capture voice` there. The folder
imports by command, with an optional schedule (a launch agent on the Mac,
daily by default, like W2.11's notes) that is the operator's configuration,
outside this repository, and not policy until the policy language exists
(Settled 4).

**Every file is marked.** Every file from the folder gets a `voice_note`
claim, with no length threshold. A whole rehearsal recorded on a phone
arrives the same way; the view flags notes over twenty minutes as possible
capture sources, and the operator rejects the claim and makes the file a
session part by ADR 0107's parts-only path (Settled 6, R6.17).

**Edited and deleted memos.** The importer never changes or deletes its
input, and never follows a deletion: a memo deleted on the phone stays in the
archive until the operator retires it (ADR 0004). A memo trimmed in the app
is new content, so a new Artifact; when its reader sees a `source_id` the
archive already holds with other bytes, the batch adds a lineage claim
(ADR 0098) from the earlier Artifact to the new one, `complete: false`,
asserted by the importer. Neither supersedes or retires the other
(Settled 5).

### Push-to-talk through the relay

A web client (the review app, the songbook, or a page of its own) records
while a button is held, with whatever audio format the browser's recorder
produces (*to be verified per browser:* WebM with Opus or MPEG-4 with AAC
are expected). Until the relay acknowledges the admission, the recording is
the client's state: a client may keep it in the browser and retry, and
nothing is archival before Door's commit (ADR 0023).

The flow is ADR 0094's member staging, unchanged:

1. The client streams the bytes to the relay's staging route. The relay
   accepts audio media types under the configured size cap and stages them
   for the caller.
2. The client sends one admission: the Artifact; a `voice_note` claim with
   route `browser` and the client's own `source_id`; a `capture_start`
   claim from the device clock (`zoned`, basis `client`, ADR 0107); and an
   origin claim. All four name the caller as `agent` and the client program
   as `by`.
3. The relay passes it when the caller holds an effective `admit` allow over
   an audience they belong to, every content row was staged for them, and
   each draft passes the member checks. Door then admits it as any batch.

A member's admission starts no enrichment (ADR 0094); the operator's next
run probes and transcribes it. A member reads what they admitted
(`readable_by` serves a member their own records); nobody else does until
the operator approves it to an audience (ADR 0088, ADR 0093).

### Which abilities apply

| Act | Ability (ADR 0094) |
|---|---|
| Send a push-to-talk note: Artifact, `voice_note`, `capture_start`, origin, together | `admit` (default off, granted per person) |
| Cut a talk span in a readable take | `cut` (default on) |
| Assert a `voice_note` about a readable Artifact or selection, or correct a title | `annotate` (default on) |
| Mark the selection's kind `talking`, or link a note to a Work or session | `annotate` |
| Set a limit on one's own note | none; a person's own say (W1.7) |

`voice_note` version 1 joins `MEMBER_CONTRACTS` under `annotate` for
readable subjects and under `admit` with staged content, as origin does
(and ADR 0107's `capture_start` likewise). These rows are part of the one
ADR 0094 amendment that lists every wave-2 ability addition (R6.10).

A note records its speaker, so its speaker may limit it. ADR 0094's
records-them test gains a clause, for limits only (R6.11): a head origin
claim naming the caller in `captured_by` selects its subject, under R5.3's
condition (the operator asserted or affirmed the origin, or it came in the
same admission as the content) and while the operator has not rejected it.
A limit only narrows (INV-26); naming oneself `captured_by` gives no
authorship and no say over sharing (ADR 0096).

### World and the relay

- **Projection.** The next projection version (ADR 0059) adds
  `arch:voice_note_route`, `arch:note_title` and `arch:source_id`, with
  their shapes; the subject is the claim's `subject` as for other claims.
  Capture starts are projected as ADR 0107 says.
- **`archive.view/voice_notes` version 1**, a named view (ADR 0082): the
  voice notes the caller can read, each with its subject, route, governing
  origin (ADR 0096), capture start (ADR 0107), duration from its audio
  stream claim, whether a transcript exists (ADR 0104), its links
  (ADR 0105) and its reach, and a `possible_source` flag over twenty
  minutes. Order is capture start descending, by a stated rule among
  several head capture-start claims (a person's before a tool's, then the
  latest by Door's recording time), every claim listed; a note with no
  `started` sorts by its claim's Door recording time and says so. A note
  whose every `voice_note` claim the operator rejected is left out and
  counted (INV-24). Its filters are route, a date range over capture start,
  and "not yet linked".
- **Talk not yet noted.** The same view, asked for a session, lists its
  `talk` suggestion spans that no `voice_note` selection covers, so a review
  client can offer "keep this as a note". These are suggestions; nothing is
  stored.
- **Search.** Transcript text of voice notes is searched through ADR 0102's
  index, filtered by the caller's readable set before results leave the
  relay (W2.7).

## Consequences

- The operator's memos reach the archive as their own, each a separate
  Artifact with its capture time, title and own-capture origin, and are found
  by date, by link, or by what they say once transcribed.
- A friend with an `admit` grant sends a voice note from a browser; one
  without sends it to the operator, who imports it naming them. Either way
  the archive records who recorded it, and only the operator and the sender
  read it until the operator approves it to an audience.
- Talk in a rehearsal take becomes a note by marking, not by copying, and
  stays in its session with its take.
- Sessions stay what ADR 0070 made them: simultaneous sources of one
  rehearsal. The rehearsal list gains no thirty-second sessions.
- One new contract, one view, one subcommand and one reader; no new Door
  state, no new relay route, no new ability.
- The open question on what marks the operator's own work across media is
  already answered by ADR 0096; voice notes are its first routine producer
  outside the recorder.

## Alternatives considered

- **Each voice note as a session of one source.** It is what `capture import
  --session-name` does today, and what the decision forbids: a note is not a
  rehearsal, and session-scoped views, participation and capture alignment
  would all have to skip it.
- **A day session for the day's notes.** A grouping nobody performed,
  forcing a session by another name.
- **Rendering each talk span to its own Artifact.** Duplicate bytes of
  evidence the take already holds; renders are admitted when chosen or sent
  (ADR 0099).
- **Selection kind `talking` alone.** It exists, but marks a span of a
  session, not a sessionless Artifact, and does not tell a note from banter.
- **`document_source` for audio.** Its kinds and media types describe
  documents; a voice note's facts (its route, its source identifier) are not
  a document's.
- **Capture time on the Artifact.** D-8 forbids it, and two people may
  disagree about it; ADR 0107's capture-start claim keeps both.
- **Capture time on the voice note.** A second place for the same fact
  beside the capture start every other reader uses (R6.15).
- **A voice-note Entity kind.** The note is its bytes, already an object; an
  Entity would be a second object for the same thing.
- **Speaker or "about" fields on the claim.** Speakers need W2.8's consent
  rule (ADR 0104) and links are W2.10's claims (ADR 0105); a field here would
  bypass both.

## New nouns

**Voice note** (`archive.claim/voice_note`).

1. It says that a recording, or a span of one, is a spoken note and how it
   arrived; nothing else does for audio outside a session, and selection
   kind `talking` does not tell a note from banter.
2. None beyond Annotation's *assert*.
3. None of its own; P-5 holds for its time through ADR 0107's capture
   start.
4. It is code inside Annotation: one contract read by one World view.
5. No: the claims Door admits are the state; the list is a view.
6. A detail: a contract and a view.

**Voice-source reader.**

1. It reads what a source application states about one file, without
   grouping, which a recorder grammar does not do.
2. None; *propose*, as the importer's.
3. None.
4. It is code inside `capture/`, beside `grammar.py`.
5. No.
6. A library.

`route` and `source_id` are payload fields, not nouns.

## Touch points

Verified on 2026-10-19 to exist; a later implementation would change:

- `capture/capture.py`: `read_directory`, `content_digest`, `known_contents`
  reused; `main` gains the `voice` subcommand; `plan` stays session-only.
- `capture/send.py`: `send` and `main` gain `--voice`.
- `capture/voice.py` (new): `capture voice`, one batch per note, capture
  start, origin and lineage drafts.
- `capture/voice_sources.py` (new): voice-source readers, the Voice Memos
  reader first, with synthetic fixtures (INV-23).
- `capture/README.md`: the new subcommand.
- `world/circle.py`: `MEMBER_CONTRACTS` and `check_member_draft`
  (`voice_note` under `annotate` and `admit`); `world/grants.py` (new,
  ADR 0094): the records-them test's `captured_by` clause (R6.11).
- `world/documents.py`: `check_document_admission`, and member staging under
  ADR 0094 for audio.
- `relay/staging.py` `Documents.stage` and `relay/served.py`
  `stage_document`, `check_contents`: ADR 0094's streamed audio route,
  nothing further.
- `world/record_views.py` `VIEWS` and `world/views.py` `catalogue`: the
  `voice_notes` view.
- `world/envelope_rules.py` `AGENT_KINDS`, and `world/contract_graphs.py`'s
  registry.
- `world/projection/6/vocabulary.ttl` and `revision.ttl`, in their
  successor version.
- A specification release under `spec/` installing `voice_note` version 1
  and Door's profile rules for it; `docs/vocabulary.md` on acceptance.

## Settled by the operator (2026-10-19)

1. **A talk span as a voice note.** A `within_recording` note is a
   `voice_note` claim about a selection; it becomes an Artifact only as a
   render when sent or kept, with lineage to the take (R6.16).
2. **May a member limit their own voice note?** Yes: `captured_by` joins
   ADR 0094's records-them test for limits only, under R5.3's condition
   (R6.11).
3. **The operator's push-to-talk over the internet.** It arrives `unknown`;
   `own_capture` is asserted later over the tailnet; W1.3 and R4.4
   unchanged (R6.13).
4. **When the Voice Memos folder imports.** By command, with an optional
   schedule configured on the Mac, daily by default; configuration, not
   policy (R6.16).
5. **Edited memos.** Both Artifacts kept, linked by importer-asserted
   lineage; never superseded or retired automatically (R6.16).
6. **Long memos.** Every file is marked; the view flags notes over twenty
   minutes, and the operator rejects the claim and adds the file to a
   session's parts (R6.16) by ADR 0107's parts-only path (R6.17).
7. **Capture time.** One capture-start claim (ADR 0107); `voice_note` drops
   `captured_at` and `clock` (R6.15).
