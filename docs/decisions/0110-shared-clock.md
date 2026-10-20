# 0110. The node is tempo master, and its clock asserts the meter

Status: proposed (wave 3, drafted 2026-10-19)

## Context

A band that plays to a click needs every timekeeper in the room to agree: the
click in the drummer's ears, a software instrument on a second laptop, a drum
machine or sequencer, and a watch tapping the beat on someone's wrist. Today
nothing says which of them leads, so each runs its own tempo and the archive
cannot say which one the band heard.

[ADR 0106](0106-set-lists-and-clocks.md) already says what a clock leaves in
the archive: a clock is a `process` Entity, one per device or program, and its
tempo map is an ordinary timed `meter_layers` version 2 claim
([ADR 0083](0083-form-and-work-meter.md)) asserted by that clock beside the
beats detected in the audio, as [ADR 0077](0077-chord-stacks-and-meter.md)'s
layers. [ADR 0107](0107-multi-device-and-live.md) makes the rehearsal node's
recording and its MIDI capture sources of the session, aligned exactly by a
`shared_clock` alignment, with a `midi_stream` claim timing a MIDI file in
microseconds. What neither says is who keeps time live, how the other devices
follow, and how that one tempo map reaches the archive.

The operator decided the following on 2026-10-19 (wave 3 decisions W3.4, with
W3.1 for where the code lives and W3.3 for the watch); this record states them
as given.

- **W3.4** The node is tempo master; software and devices sync by Ableton
  Link, hardware by MIDI clock out through the interface, and the watch click
  follows the node. The clock's tempo map is a meter claim (ADR 0106). Link's
  licence must be checked against AGPL before any of it enters the archive
  repository; Link stays in the node application.
- **W3.1** The rehearsal node is a separate application repository, like the
  review app; live analysis code lives in the archive's `enrich/`.
- **W3.3** The watch is reached first through a self-hosted push service and a
  home-screen PWA (a spike), later through a native companion app.
- **Earlier, settled:** live is advice, batch is evidence; a click's or MIDI
  clock's tempo map is a meter claim asserted by the clock.

## Decision

### One tempo map, kept by the node

The node runs **one clock**: a tempo, a time signature with its grouping, a
beat unit, and a running state (started or stopped), changed only by the
operator at the node (or by the node from the set list's target tempo when the
operator moves to an entry, ADR 0106). That clock's history over the
rehearsal is **the tempo map**. Everything else that keeps time is a
**follower** that renders it and asserts nothing of its own:

| Follower | How it follows | Recorded? |
|---|---|---|
| The click on the node's audio output (ears, monitors) | rendered from the tempo map, accented on downbeats | as the clock track (below); a click channel on the mixer is extra evidence |
| Link peers (software, other laptops) | the node is a Link peer whose session tempo, beat and phase are the map's; the Link quantum is the bar's beat count, so peers agree on the downbeat | no: Link's session state is transport, not evidence |
| Hardware (drum machine, sequencer, synth arpeggiators) | MIDI clock out through the interface: Start, Stop, Song Position, 24 ticks per quarter at the map's tempo | no: what a follower played is in its audio, aligned like any source |
| The watch click | the node sends the next beats' times and accents ahead; the watch schedules taps against its own clock | no: a late tap changes nothing in the archive |

**Link has no master in its own protocol**: any peer may change the session
tempo (*unverified*: stated from knowledge of the protocol, not checked
against Link's documentation for this record). "The node is tempo master" is therefore the node's policy, not Link's:
when a peer changes the tempo, the node does not follow; it restores the map's
tempo and shows the attempt to the operator (Open 2). A follower that drifts or
drops out is a fault of that follower, never a change to the map.

### The tempo map reaches the archive as the node's MIDI capture

The node already writes a MIDI capture that ADR 0107's node grammar imports as
a session source, aligned to the node's audio by `shared_clock`. The node
writes that file as a **Standard MIDI File format 1** whose first track, the
**clock track**, carries the tempo map in MIDI's own terms, so no new contract
is needed:

- tempo meta events (`FF 51`) at every tempo change, and time signature meta
  events (`FF 58`) at every meter change, whose clocks-per-click field states
  the beat unit the click counts (36 for a dotted quarter in 6/8);
- marker meta events (`FF 06`) `clock start` and `clock stop` at each running
  interval's ends; the clock asserts beats only inside them (ADR 0106: a gap
  asserts nothing);
- a text meta event (`FF 01`) at time zero stating the node software and the
  output latency it compensated, so the clock track's beat times are when the
  click left the interface, on the node's input sample clock;
- the operator's other MIDI (a keyboard) in later tracks, on the same ticks.

The file's `midi_stream` claim (ADR 0107) times every event in microseconds
from those tempo events, so each beat the clock played has an exact time on
the node's timeline.

### The clock's claim is ADR 0106's, with the node's MIDI capture as evidence

After the rehearsal, `enrich/clock.py` (ADR 0106's new reader) reads the
clock track and proposes ADR 0106's claim unchanged:

- envelope `agent`: the node clock's `process` Entity ("Node metronome"), one
  per node program, reused across sessions (ADR 0106, Settled 7); `by`: the
  reader's Package URL;
- `source` and `stream`: the node's audio recording, on whose sample clock the
  clock ran; beat times are mapped from microseconds through the
  `shared_clock` alignment, exact up to its stated bound;
- `run`: a derivation run whose `used` pins the MIDI Artifact, its
  `midi_stream` claim and the alignment, and whose parameters carry the
  clock track's latency statement;
- **one beat-only layer** labelled with the clock's name, `beat_unit` from the
  clocks-per-click field, `downbeats` because the node accents them, and a
  **bars layer** because the node's own configuration states the time
  signature (ADR 0106's rule, recorded in the run's parameters).

A click channel recorded on the mixer can yield a second claim by the same
clock, read from audio onsets, beside the first (INV-7); the reader proposes
it only when no node clock track covers that span (Open 4).

### When the band drifts from the click

The band and the click are **two meter layers, never a correction**. The
clock's claim says what the clock did; a beat tracker's claim over the same
source says what the band played; both stand, attributed to different agents,
and neither supersedes or adjusts the other (INV-7, ADR 0106). The tracker
never snaps its beats to the click, and the clock's claim is never edited to
meet the band. How far the band ran from the click is a comparison between
the two layers, which is the open question of matching beat grids
([open](../open-questions.md#claims-and-provenance)); until it is answered,
World shows both. Live, the node may show a drift hint (advice, never
admitted). The node never follows the band: tempo following is out of scope
(Open 3).

### Where the code lives, and the licence precondition

- **Node application** (separate repository, W3.1,
  [ADR 0109](0109-rehearsal-node.md)): the clock, the click,
  MIDI clock out, the Link peer, the watch feed, and writing the clock track.
  Link is linked here and only here.
- **Archive** (`enrich/`, `capture/`): reading the clock track from a
  Standard MIDI File into ADR 0106's claim. It needs no Link code, no Link
  dependency and no Link headers, because the tempo map arrives as MIDI.
- **Precondition.** Before any Link code, header, binding or vendored copy
  enters the archive repository, someone checks Link's licence terms against
  `AGPL-3.0-or-later` ([ADR 0017](0017-licensing.md)): the licence the
  upstream repository states, whether it allows "or later" upgrade to a
  version compatible with AGPL, the licences of its bundled dependencies, and
  any naming or logo conditions. The result is recorded in a decision record
  or in `REUSE.toml`'s annotations. That check has not been done; nothing in
  this record assumes its outcome. Live analysis in `enrich/` that needs a
  tempo gets it from the node, never through Link; how the node passes its
  clock to [ADR 0108](0108-live-analysis.md)'s live runner, whose standard
  input carries only audio, is not settled.

## Consequences

- One tempo map rules the room, and the archive holds exactly that map, as
  the clock's claim, with no new contract, view or route.
- A follower's faults (a Link peer that wandered, hardware that missed Start)
  never reach the record as tempo; they show up as that follower's audio
  disagreeing with the clock, a fact any reader can see.
- The band's drift from the click is visible as two layers side by side,
  which is the reason to keep both.
- Archive code stays free of Link whatever the licence check finds; only the
  node application carries that risk.
- No invariant changes. INV-7 holds because the clock's and the tracker's
  layers coexist; INV-2 because nothing live is admitted; the clock's claim
  arrives only after the node's recording is admitted.

## Alternatives considered

- **Link as the master, the node one peer among equals.** Any laptop could
  then change the tempo, and the archive would have no single asserter to
  name; ADR 0106's clock would be the whole session, not a device.
- **The node follows the band (tempo following).** The click would then be
  derived from the band and its claim would no longer be independent evidence
  beside the tracker's.
- **A drum machine as master, the node a follower.** Hardware cannot follow
  a set list's target tempo, and its clock reaches the archive only if
  someone records its MIDI; it stays possible under ADR 0106 when no node runs.
- **A new clock-log contract or file format.** A Standard MIDI File already
  states tempo, meter and beat unit, ADR 0107 already admits and times it,
  and every MIDI tool can read it.
- **Correcting the tracker's beats to the click, or the click to the band.**
  Either destroys one of the two pieces of evidence the drift is measured by.
- **Link inside the archive's `enrich/` for live tempo.** It would put an
  unchecked licence in an AGPL repository, against W3.4.

## New nouns

- **Tempo master.** (1) It names the one device whose clock every other
  timekeeper follows. (2) No verb in Archive. (3) None itself; it gives
  ADR 0106's clock a single asserter per session. (4) It is not Archive code:
  it is the node application's role. (5) No. (6) A role of an application,
  not a subsystem.
- **Clock track.** (1) It carries the node's tempo map inside the MIDI capture
  ADR 0107 already admits. (2) No verb; the node writes it, `enrich/clock.py`
  reads it. (3) None. (4) It is a convention of a Standard MIDI File, read by
  code inside an existing concept (Enrichment). (5) No; the claim derived from
  it is evidence, the file is content. (6) A file convention, a detail.
- **Follower** is used in its ordinary sense and is not added to the
  vocabulary.

## Touch points

- `enrich/clock.py` (new, declared by ADR 0106): reading a clock track's
  tempo, time signature and marker events through the file's `midi_stream`
  timeline into the clock's meter claim and run.
- `enrich/probe.py`: `probe`, which ADR 0107 extends to propose `midi_stream`;
  the clock track's tempo events are what make its microsecond times exact.
- `capture/grammar.py`: ADR 0107's node grammar (new) recognises the node's
  MIDI file; `capture/capture.py`: `plan` proposes the `shared_clock`
  alignment and, through ADR 0106's hook, the clock's claim.
- `world/harmony.py`: `beat_tempi` and `check_meter_layers`, and
  `world/form.py`: `check_meter_layers_v2`, unchanged.
- `REUSE.toml` and a decision record: where the Link licence check's result
  is recorded, if Link ever enters the repository.
- The node application repository (outside Archive): the clock, its
  followers, and the clock track writer.

## Open for the operator

1. **The licence check: who does it, and when?** Recommended: do it before
   the node application is first published, not only before Link enters the
   archive repository, since the node application will be shared too; record
   the result as a short decision record. ADR 0109 (Open 7) makes the node
   repository's own licence depend on it.
2. **A Link peer changes the tempo.** Recommended: the node restores its own
   tempo at the next beat and shows "tempo change from a peer ignored"; the
   operator can adopt it with one action, which then becomes the map's.
3. **Tempo following.** Recommended: none in the first version; if wanted
   later, a following clock is a different `process` Entity, so its claim is
   never mistaken for an independent click.
4. **Two claims from one clock.** When the click is also recorded on the
   mixer, recommended: propose only the clock-track claim, and read the click
   channel only for spans no clock track covers (another clock's click, or a
   node crash).
5. **Where the clock track convention is written down.** Recommended: in
   `enrich/clock.py`'s documentation and the node repository, not in
   `spec/`: the file is content and the claim is ADR 0106's, so the
   convention binds two programs, not every archive reader.
6. **Watch click timing.** Recommended: treat the watch tap as unverified
   until ADR 0109's spikes measure its jitter; the click in the ears stays the
   reference, and nothing about the watch is recorded.
