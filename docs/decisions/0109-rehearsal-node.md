# 0109. The rehearsal node is an application, and the watch is a display and a voice

Status: accepted (operator, 2026-10-19); proposed in wave 3 and its open
questions settled the same day

## Context

[ADR 0107](0107-multi-device-and-live.md) fixed what a rehearsal node leaves
behind: files a node grammar recognises, an append-only outbox of response
groups with three placeholders, and a `shared_clock` alignment for its own
audio and MIDI, all entering after the rehearsal through `capture import` in
operator capacity. It called the node "a client program, as the review app
is" and left the program itself, its notifications and the watch to wave 3.
What is still open:

- **Where the node's code lives**, and what it may take from Archive. The
  review app shows the pattern (`review-app/AGENTS.md`): a sibling
  repository that depends only on what Archive publishes, reads World and
  proposes to Door, and never imports Archive's source.
- **How a response made away from the node** (a tap on a watch, a reply on a
  phone) becomes durable, given that ADR 0107 makes the outbox the only
  durable place and anchors every response to the node's recording.
- **How notifications reach a phone and a watch** during a rehearsal, and
  which platform features that needs, none of which has been tried here.
- **What a phone or tablet is** once a native app replaces the interim Voice
  Memos recording (ADR 0107).

The operator decided the following on 2026-10-19 (wave 3 decisions W3.1 and
W3.3); this record states them as given and takes the node half of W3.1.
The analysis half (live analysis code in `enrich/`), W3.2's streaming
extractor contract and W3.5's practice feedback are
[ADR 0108](0108-live-analysis.md)'s, and W3.4's clock is
[ADR 0110](0110-shared-clock.md)'s; they are cited, not decided, here.

- **W3.1.** Live analysis code lives in the archive's `enrich/` (live
  analysis is Enrichment); the rehearsal node is a separate application
  repository like the review app (device I/O, outbox, UI, phone link).
- **W3.3.** Notifications and watch responses: spike a self-hosted push
  service on the tailnet with action buttons and web push from a home-screen
  PWA first (unverified whether actions or voice replies work on the watch);
  the destination is a native iOS/watchOS companion app, which is also the
  phone/tablet mic node (background recording).

The implementation order (P.2) puts live last; nothing here is built.

## Decision

### The boundary

**The node is an application in its own repository, outside Archive.** It
stands to Archive as the review app does: a client that reads World through
the relay and whose writes reach Door only through Archive's own tools. It
appears in [boundaries.md](../boundaries.md) beside the review app as a
sibling project, not as a concept of Archive.

| The node owns | The node never owns |
|---|---|
| device I/O: the audio interface, MIDI in and out, the click | archival meaning: no claim is a record before Door commits it (INV-2) |
| its own recording, written as files the node grammar recognises | what a response *means*: drafts take Archive's contracts as they are |
| the durable outbox of response groups (ADR 0107) | any write during the rehearsal: nothing is proposed live |
| its UI, and the link to phones and the watch, push included | the analysis code: streaming extractors and the live runner live in `enrich/` (ADR 0108) |
| a cache of the World answers it read before the rehearsal | a second copy of Archive's rules: Door's refusal at flush is the check |

**It proposes only after the rehearsal**, by the paths that exist: its files
and outbox are handed to the archive machine and imported with `capture
import`, which admits the recording, waits for the stream probe, and flushes
the outbox group by group (ADR 0107). It never calls Door's service, and the
relay gains no route for it in this record. A laptop node hands over with
`capture send`; a phone or tablet, which cannot run it, writes its bundles to
a folder synced to the archive machine, as Voice Memos does, until a later
record adds a relay route for node bundles (tailnet, operator capacity only,
staging then `capture import`) (Settled 1).

### What Archive publishes for the node

The node depends on these and on nothing else of Archive. Contracts and
formats are pinned as the review app pins its contracts (`{id, version,
digest}`), never by an Archive commit. The live runner (item 4) runs from a
checkout of this repository at a tagged release, which the node pins, and
each streaming extractor is pinned by its id, version and digest (R7.2,
ADR 0108):

1. **The read side.** Before a rehearsal the node reads, through the relay
   over the tailnet in operator capacity, the Work Entity revisions, set
   lists ([ADR 0106](0106-set-lists-and-clocks.md)) and clock Entities it may
   pin, with the projection version it queried (ADR 0059). It keeps them as
   a cache for the evening; during the rehearsal it needs no connection to
   Archive. The same reads fetch the reference profiles of the Works it
   may hear, for ADR 0108's live song guess, which the node hands to the
   live runner (R7.1), and the pad maps of the sampler banks it may see,
   for [ADR 0111](0111-pad-maps.md)'s live pad display (ADR 0111,
   Settled 4). Today's relay serves no reference profile; the read that
   serves them is added when the live song guess is built. A pinned
   revision that Door later refuses fails only its own group (ADR 0107).
2. **The outbox format.** ADR 0107's entry shape (a response group of drafts
   in their contracts' shapes, with the `node`, `node_stream` and `outbox`
   placeholders) is published as a JSON Schema beside the importer,
   `capture/outbox.schema.json`, with a format version in every entry. The
   importer refuses an entry whose version it does not know and reports it,
   as it reports a refused group.
3. **The bundle layout.** What the node grammar recognises: the recording's
   parts with rollover, its MIDI, the outbox file, and a `capture_start` per
   part from the device clock (`basis` `client`). One grammar serves every
   device that runs the node software, laptop or phone; the device is the
   source token in `session_parts` version 2.
4. **The live runner** (ADR 0108), `enrich/live.py run`, which the node
   starts as a child process, writing its recorded sample frames to its
   standard input, optionally its clock events to a second named pipe
   (R7.3, ADR 0110), and reading `archive-live-event/1` lines from its
   standard output. The node never imports `enrich/` as a library, so
   Archive's code and the node's (Ableton Link included, ADR 0110) stay
   separate works. Link's licence is checked before the node is first
   published, and the node repository is `AGPL-3.0-or-later` if the check
   passes (Settled 7, ADR 0110).
5. **Identity.** Every draft's `agent` is the operator's person and its `by`
   is the node software's Package URL (W2.13); a companion app has its own.

### Every outbox belongs to one recording

**A device that records may hold an outbox, and its outbox is anchored to
that device's own recording.** This is ADR 0107's rule applied per device:
the laptop node's outbox anchors to the laptop's recording; a companion app
recording on a phone (below) anchors its outbox to the phone's recording.
A response made on the watch goes to whichever recording device it is linked
to. Capture alignment then carries every response onto the other sources, as
ADR 0107 already says, so no device needs to know another's samples.

**Only the operator's devices hold an outbox.** A member's phone records as
a mic node only, with no outbox, and its recording is admitted under `admit`
(ADR 0094): the `node_stream` placeholder needs the stream probe, which a
member's admission never starts, so a member's outbox could not be flushed.
Responses come only from the operator's devices until someone needs more
(Settled 2, R7.4; consistent with ADR 0107).

**A response is taken only when an outbox holds it.** The watch or phone
that originates a response gives it a response identifier and sends it to
its node; the node turns it into a response group (its span chosen by the
node, ADR 0107), writes and syncs the entry, and only then acknowledges.
The span a gesture makes is a node setting, by default the ten seconds
before the gesture clamped to the recording's start, with a start-now and
end-now pair on the watch for a deliberate cut (Settled 5).
The watch shows a response as taken only on that acknowledgement, and
resends with the same identifier until it gets one; the node keeps the
identifier in the entry and answers a repeat with the entry it already
wrote. A response that never reached an outbox is lost, and the watch says
so rather than showing it as taken.

### The watch: a display and a voice

**As a display** the watch shows what the node offers as advice: a song
guess, a set-list position, a tempo or tuning hint, and the click that
follows the node (ADR 0110). All of it is operational and never written to an
outbox unless the operator acts on it (ADR 0107, "live is advice").

**As a voice** the watch answers. Two kinds of answer exist:

- **An action**: a button on a notification or in the app ("yes, it is
  *Lantern Song*", "next on the set list", "mark this"). Each maps to one of
  ADR 0107's response kinds: a Work link, a set-list edit, a mark, a cut, a
  title from a list the node offered, or a kind.
- **A spoken reply**: kept as audio, not as text. A spoken reply is a short
  recording the watch sends to its node, which keeps it in its bundle as a
  part of a watch source. Text from it comes later from local ASR
  ([ADR 0104](0104-transcription-and-speakers.md)); system dictation is not
  used for any draft, because ASR runs locally only and where a platform's
  dictation runs is unverified. The reply's response group drafts a
  selection and a mark on the node's recording at the moment of the reply;
  the clip enters as a watch source, and a `within_recording` voice note
  ([ADR 0103](0103-voice-capture.md)) on it is proposed after alignment. No
  new placeholder is needed (Settled 3, R7.8).

### Notifications: the plan (W3.3)

**Notifications are the node's, and Archive never sends one.** They carry
advice outward and answers back; losing one loses nothing archival.

**A push payload carries no title, name or lyric**, because it crosses the
platform's push servers outside the tailnet; the companion app fetches the
text from the node over the tailnet, and the web-push spike uses Works with
invented titles only (Settled 4, R7.9). The companion app's direct link to
the node is the main path and push the fallback, since push needs the
internet and a rehearsal room may have none; the spikes measure both
(Settled 6).

**Two spikes come first, then the companion app.**

1. **A self-hosted push service on the tailnet**, run by the node's host,
   sending notifications with action buttons to the phone. The spike
   answers, on real devices: whether the notification reaches the watch,
   whether its action buttons appear and work there, whether a spoken or
   typed reply is possible, how long a tap takes to reach the node, and
   whether delivery works with the phone locked and with the rehearsal room
   off the internet. *Unverified:* every one of these, and whether a
   self-hosted service can wake an iPhone without relaying through an
   upstream service.
2. **Web push from a home-screen PWA** served by the node over tailnet
   HTTPS, asking the same questions. *Unverified:* whether web push on iOS
   shows action buttons at all, whether such a notification is mirrored to
   the watch with them, and whether it can be answered there.

The spikes' results are written into this record's successor; neither
spike's code enters Archive.

**The destination is a native iOS and watchOS companion app**, in the
node's repository or a sibling of it. It is three things:

- **the watch's display and voice**, talking to the phone and through it to
  the node, with push as a fallback rather than the main path;
- **a mic node**: on a phone or tablet it records its own source in the
  node bundle layout, replacing the interim Voice Memos recording (ADR 0107).
  *Unverified:* continuous recording with the screen locked or the app in the
  background for a whole rehearsal, on iOS and iPadOS, and recording on the
  watch;
- **a node** when no laptop is there: the same software, holding its own
  outbox. It sends no live hints until it has streaming builds held to ADR
  0108's replay test (ADR 0108, Settled 6).

## Consequences

- This record gives Archive no program, route, contract or dependency; the
  live runner and its event format are ADR 0108's. The node is replaceable
  without touching Archive, and Archive is testable without it.
- The only artefact this record adds for the node is the outbox schema, so
  the node and the importer cannot drift on the format they share for
  writes.
- A response survives a dropped notification, a crash of the watch, and a
  lost connection to the node, as long as the originating device keeps
  resending; it is durable only once an outbox holds it.
- Spoken replies cost a recording per reply and reach text only after the
  rehearsal, through local ASR.
- Until the spikes, nothing about notifications on the watch is known; the
  node must work with the watch absent.
- No invariant changes.

## Alternatives considered

- **The node inside Archive** (`node/` beside `relay/`). It would bring audio
  devices, a UI, push and Ableton Link into an AGPL repository whose
  boundaries leave web applications out on purpose; W3.1 rules it out.
- **The node writing live through the relay.** It would admit evidence
  before its recording exists, against ADR 0107's flush order, and need a
  connection in the rehearsal room.
- **The watch writing its own outbox.** A watch records nothing to anchor
  to; a third anchor type would need a new placeholder for every watch.
- **Dictated text as a reply.** Quicker to read, but its ASR may run off the
  device, and a mishearing would enter as the operator's title.
- **Native app first, no spikes.** W3.3 orders the spikes first; they say
  cheaply which watch features are worth a native app.

## New nouns

None for Archive. The words below name things outside it, and each fails the
test in [vocabulary.md](../vocabulary.md#adding-a-noun) as an Archive noun:

- **Rehearsal node** (ADR 0107's noun, made precise). (1) It records at a
  rehearsal and holds responses until its recording is admitted. (2) None in
  Archive. (3) None itself; it keeps INV-2 by proposing nothing live.
  (4) It is not Archive code: a sibling application (W3.1). (5) No; its
  outbox and cache are operational. (6) An application.
- **Mic node.** (1) A device recording one source with the node software.
  (2) None. (3) None. (4) In Archive it is a source (ADR 0070, ADR 0107).
  (5) No. (6) A role of the companion app.
- **Companion app.** (1)–(3) None in Archive. (4) It is the node's
  application on phones and the watch. (5) No. (6) An application.
- **Push service.** (1) Delivers notifications. (2)–(3) None. (4) Fleet
  infrastructure the node uses; Archive never depends on it. (5) No.
  (6) A dependency of the node.
- **Response identifier.** A field between a device and its node, kept in
  the outbox entry; a protocol detail of the outbox schema, not a contract.

The vocabulary's *rehearsal node* entry says it is a sibling application.

## Touch points

- `capture/outbox.schema.json` (new): the published outbox entry schema,
  with its format version and the response identifier.
- `capture/outbox.py` (new, ADR 0107): checks entries against the schema.
- `capture/grammar.py`: ADR 0107's node grammar, beside `ZoomH4E` in
  `GRAMMARS`, recognising any device's bundle by source token.
- `capture/send.py`: carries the outbox with a laptop node's files to the
  archive machine's inbox, as it carries a card.
- `capture/capture.py`: `chain` (ADR 0107's flush) refuses unknown entry
  versions.
- `relay/openapi.json`: unchanged; the read side is today's World query and
  views.
- `docs/boundaries.md`: the node beside the review app as a sibling project.
- `docs/vocabulary.md`: the *rehearsal node* entry.

## Settled by the operator (2026-10-19)

1. **How a phone node hands over.** Through a folder synced to the archive
   machine now, as Voice Memos does; a relay route for node bundles
   (tailnet, operator capacity only, staging then `capture import`) later,
   in its own record (R7.5).
2. **Members' phones record as mic nodes only,** with no outbox, their
   recording admitted under `admit`; responses come only from the
   operator's devices (R7.4; consistent with ADR 0107).
3. **A spoken reply and its anchor.** A selection and a mark on the node's
   recording at the moment of the reply, the clip as a watch source, and a
   `within_recording` voice note on it after alignment; no new placeholder
   (R7.8).
4. **What a notification may say.** No title, name or lyric in a push
   payload; the companion app fetches the text over the tailnet; the
   web-push spike uses invented titles only (R7.9).
5. **The span a gesture makes.** A node setting, by default the ten seconds
   before the gesture clamped to the recording's start, with a start-now and
   end-now pair on the watch (R7.12).
6. **Rehearsal rooms without internet.** The companion app's direct link to
   the node is the main path and push the fallback; the spikes measure both
   (R7.12).
7. **The node repository's licence.** Link's licence is checked before the
   node is first published; the node repository is `AGPL-3.0-or-later` if
   the check passes (R7.7, ADR 0110).
8. **Reference profiles and pad maps on the read side.** The node fetches
   the Works' reference profiles before the rehearsal through a relay read
   (R7.1), and pad maps with the rest of its read side (ADR 0111, R7.12).
9. **Pinning.** Extractors by id, version and digest; the live runner by a
   tagged release (R7.2).
