# 0111. A pad map says what a sampler plays, and a proposer turns a session's MIDI into lineage

Status: proposed (wave 3, drafted 2026-10-19)

## Context

A sampler at a rehearsal plays one-shots, loops and cut phrases from its
banks, and those sounds end up in the session's recording. Wave 2 left
everything in place except the step between them:

- **A sampler bank is a collection** ([ADR 0100](0100-packs-and-collections.md)):
  a `collection` Entity whose members, Artifacts or `audio_selection`
  revisions, are given by `collection_members` claims, each member with an
  optional `slot` ("A-01", "pad 7"). A slot is a position as the card names
  it. Nothing says which MIDI note or channel fires it, which bank a pad
  belongs to, or how it plays (to the end once struck, only while held, or
  looping).
- **MIDI is a capture source** ([ADR 0107](0107-multi-device-and-live.md)):
  `midi_stream` gives a Standard MIDI File a timeline in microseconds, and
  `capture_alignment` maps it onto the session's audio exactly when the two
  share a clock. `enrich/midi_notes.py` turns MIDI into notes claims
  ([ADR 0086](0086-notes-and-melody.md)), so a pad struck on channel 1 reads
  as a pitch and not as a sample.
- **Lineage can say a recording used a sample** ([ADR 0098](0098-content-lineage.md)):
  one `output` Artifact, up to 64 inputs (Artifacts or selections), and
  lineage spans placing `from` in an input at `to` in the output, asserted
  by any person or by a tool with a `run`. Lineage only narrows reach, and
  destroy follows it. ADR 0107 recorded "performance lineage (which pad fired
  which sample when) waits for a later wave", and ADR 0100 did the same.
- **Machines already propose through runs** ([ADR 0064](0064-derivation-runs.md),
  [ADR 0067](0067-matches-and-alignments-pin-runs.md)): a run is a claim, it
  pins what it read in `used`, and its outputs travel with it in one batch.

The operator decided the following on 2026-10-19 (wave 3 decisions W3.6
and W3.7); this record states them as given.

- **W3.6 Pad maps.** A `pad_map` claim on a sampler-bank collection says
  which pad holds which sample; a machine proposer reads a session's MIDI
  capture through it and proposes lineage placements. `pad_map` is the only
  new contract.
- **W3.7 Sample identification.** A spike, on a public or test corpus
  first, locally, no third-party uploads; friends' tracks with known samples
  once custody is in place; output is machine-proposed lineage claims
  (ADR 0098).

The program order (P.2) puts lineage first and builds nothing now. This
record is a contract on paper.

## Decision

### `archive.claim/pad_map` version 1

A **pad map** says, for one sampler-bank collection as one device was set
up at one time, which sample each pad holds, which bank the pad is in, what
MIDI message fires it, and how it plays. It is the device's statement about
its own bank, which a membership claim's slot (organisation only) does not
make.

| Role | Count | Pins |
|---|---|---|
| `collection` | exactly 1 | an Entity revision of kind `collection` |
| `sample` | 1 to 1,000, distinct objects | an Artifact revision, or an `archive.claim/audio_selection` Claim revision (version 2, or ADR 0099's version 3 once installed): what the pads hold |
| `document` | 0 or 1 | an Artifact: the bank or project file the map was read from, admitted whole and unparsed when proprietary (ADR 0100, Settled 7) |
| `run` | 0 or 1 | a `derivation_run` version 1 Claim revision, when a bank grammar read the map |

Its payload is closed:

```json
{
  "observed_at": "2026-10-12",
  "device": "sixteen-pad sampler, firmware 4",
  "banks": [
    {"bank": "A", "select": {"channel": 1, "program": 0},
     "pads": [
       {"pad": "A-01", "sample": 0, "trigger": {"channel": 1, "note": 36}, "play": "one_shot"},
       {"pad": "A-02", "sample": 1, "trigger": {"channel": 1, "note": 37}, "play": "gate"},
       {"pad": "A-03", "sample": 2, "trigger": {"channel": 1, "low": 48, "high": 72, "root": 60}, "play": "gate"},
       {"pad": "A-04", "sample": 3, "play": "loop"}
     ]}
  ]
}
```

- `observed_at` (required): when the device was set up this way, an
  RFC 3339 full date or UTC instant. A pad map is true of a moment; a
  reloaded card is a new revision, and the earlier one stays as history
  (INV-1).
- `device` (optional, 1 to 200 characters): free text naming the device
  and mode. Nothing parses it.
- `banks` (required, 1 to 64): each with `bank` (1 to 32 characters,
  distinct in the claim), optional `select`, and `pads` (1 to 1,000 in all
  banks together).
  - `select`: the MIDI message that makes this bank active, `channel`
    (1 to 16) with `program` (0 to 127) and optional `bank_msb` and
    `bank_lsb` (0 to 127, control changes 0 and 32 sent before the program
    change). No two banks share a `select`. A device that switches banks
    only by hand has none.
- A pad has `pad` (1 to 32 characters, distinct within its bank; the same
  spelling as the member's `slot` when there is one), `sample` (an index
  into the `sample` references), optional `trigger`, and optional `play`.
  - `trigger`: `channel` (1 to 16) and either `note` (0 to 127) or a key
    range `low`, `high`, `root` (0 to 127, `low <= root <= high`), for a
    sample played chromatically across keys. No two pads of one bank share
    a channel and note. A pad without a trigger is held but never fired by
    MIDI (it is played by hand and sends nothing).
  - `play`: `one_shot` (plays to its end once struck), `gate` (plays while
    held), or `loop` (repeats while held). Absent says nothing.
- Every `sample` reference is used by at least one pad; one sample on two
  pads is two pads naming one index.

**Door rules** (a specification release adds them with the bundle): the
`collection` kind; distinct samples of the allowed shapes; every index in
range and every reference used; bank names, selects, and pad names
distinct as above; triggers distinct within a bank and key ranges well
formed. The agent is the operator, or a bank grammar's process with a
`run`.

**A pad map is not membership.** It adds no member, and a sample it names
need not be in the collection's membership (a pad can hold something
loaded since the card was imported). It pins its samples, so the claim
itself reaches no further than its strictest sample; World's pin map gains
no edge from a sample to the collection or the map, as for membership.
Several pad maps of one collection coexist (two devices, two modes); none
is current, and the proposer never chooses one (below).

**Where pad maps come from.** ADR 0100's bank grammar, for a device whose
card layout and documented note numbering give each slot a trigger,
proposes a pad map beside the membership claim when it imports a card,
asserting through its run, with the card's bank file as `document`. Where
assignments live only in a proprietary project file, or the operator
changed the device's numbering, the operator writes the map:
`capture padmap --collection <object id> <map.json>` (new) proposes one
pad map from a small JSON file naming each sample by object ID, in
operator capacity, through `door admit`.

### The pad proposer

A machine proposer, `enrich/pad_lineage.py` (new), reads one session's MIDI
capture through one pad map and proposes ADR 0098 lineage claims whose
output is the session's audio and whose inputs are the samples the pads
fired. It is a derivation run (ADR 0064) asserting through its process
agent, and runs only when the operator names its inputs:

- **The MIDI and the map are named, never guessed.** The operator names
  the MIDI Artifact, optionally its track and channels (in a rehearsal
  node's MIDI capture, a track after
  [ADR 0110](0110-shared-clock.md)'s clock track), and the pad map
  revision. Which card was loaded at a rehearsal is not in the MIDI, so a
  wrong guess would put another bank's samples into the recording's
  lineage and narrow its reach for nothing.
- **The output is named, or is the primary source.** Each lineage claim's
  `output` is one part of one audio source of the session (ADR 0107):
  the source the operator names (the sampler's own mixer channel is the
  most exact), else the session's primary source.
- **Time goes through alignment.** Each event's microsecond is mapped onto
  the output part through `aligned_spans`'s choice rule: the pair's only
  `capture_alignment`, or the operator-affirmed one among several. With
  neither, the proposer proposes nothing for that part and says why. A
  `latency_us` parameter (default 0) shifts every event by the device's
  stated output latency.
- **Banks follow the MIDI.** The active bank starts as the one the operator
  names, else the map's only bank; a program change (with its bank
  selects) matching a bank's `select` switches it. A note while no bank is
  known, or that no pad of the active bank triggers, is skipped and
  counted.
- **One span per note.** A note-on with velocity above 0 on a pad's
  trigger becomes a lineage span naming that pad's sample as `input`:
  `to` starts at the mapped note-on; it ends at the mapped note-off for
  `gate`, `loop`, or an absent `play`, and for `one_shot` at the note-on
  plus the sample's length (from its `audio_stream`, or the selection's
  spans) converted to the output's rate. `from` is the whole sample for
  `one_shot` and absent otherwise. A key-range pad adds a `pitch` transform
  of `(note - root) × 100` cents. No other transform is stated, so the
  list is absent unless it is a pitch (velocity is not read as gain). A
  span past the part's end is clipped; a note across a rollover gives one
  span in each part's claim.
- **Claims stay within ADR 0098's bounds.** One claim per output part,
  split into several when a part fires more than 64 distinct samples or
  more than 10,000 notes; `complete` is never set, because the recording
  also holds everything played live. Every claim pins the same `run`,
  whose `used` lists the MIDI Artifact, its `midi_stream` claim, the
  alignment revision, the pad map revision, and the output's
  `audio_stream`; its `parameters` hold the named track, channels, bank and
  latency.

A pad map naming a selection gives a lineage input that is a selection,
which ADR 0098 already allows; a sample cut from the same recording
(resampled at the rehearsal) forms a lineage cycle, which ADR 0098
tolerates with one strictest reach.

### What this extends

- **ADR 0098** gains its first machine proposer of placements. Its
  contract, Door rules, reach, judgment and destroy are unchanged: the
  proposer is a tool's Package URL with a `run`, which its agent rule
  already admits. The operator's `rejected` sets a wrong placement aside
  for reach; nothing else does.
- **ADR 0100**'s bank grammar may propose a pad map with the membership
  claim; its Settled 7 (proprietary project files unparsed) stands, and
  the hand-written map is how such a device gets one.
- **ADR 0107**'s performance-lineage deferral is answered; its MIDI stream,
  alignment and `aligned_spans` are read, not changed.
- **ADR 0094**'s ability table: a pad map is the operator's or a bank
  grammar's, never member capacity; the pad proposer's lineage and run are
  machine provenance, never member capacity. No member contract changes.
- **ADR 0097**: a sample reached through pad lineage has no recipe, so its
  rights are read for `derive`, as for any lineage without one.

### Sample identification (W3.7) feeds the same path

The identification spike is future work. Whatever it becomes, its output
is ADR 0098 lineage claims, output the recording and inputs admitted
samples, asserted by its own run, judged and read for reach and destroy
exactly as the pad proposer's are. It needs no pad map, and covers
samplers that send no MIDI. A pad placement and an identified one about the
same moment stand side by side (INV-7). This record does not specify its
method, corpus or thresholds; P.2 and W3.7 keep it local and off
third-party services.

### World

- **Projection.** A pad map projects as a resource per pad under `arch:`
  terms (bank, pad, trigger channel and note or key range, play),
  linked to the collection and to the sample it holds. The pad proposer's
  lineage projects as any lineage claim.
- **Views.** None new. ADR 0098's `derived` view already answers "which
  sessions used this sample", and `lineage` answers "which samples does
  this recording use, where".

## Consequences

- A rehearsal recorded with the sampler's MIDI says, span by span, which
  sample sounded where, and the sample app's "used in" lists sessions.
- **Running the proposer can narrow a shared recording's reach.** A
  recording that fired a friend's crate sample of unknown origin, or a
  pack one-shot whose licence permits `derive` only in the circle, reaches
  no further than that sample from the moment the lineage is admitted;
  a bandmate may lose sight of a rehearsal they could read. That is
  W1.13's asymmetry, and the operator's rejection is the remedy.
- **Destroying a sample now names sessions.** Every recording whose pad
  lineage names a destroyed sample is a dependent needing a disposition,
  judged or not (ADR 0098); `dependent_source_gone` keeps the recording.
  A wrong pad map's lineage costs one disposition each, never a lost
  recording.
- Placements are as good as the pad map and the alignment; both are pinned
  in the run, so a wrong one is found and its claims rejected together.
- A 10,000-note part is one or a few claims, not thousands.
- No invariant changes; no accepted contract changes.

## Alternatives considered

- **A trigger field on `collection_members` (version 2).** Membership is
  organisation and never device state; a bank's numbering changes with the
  device's mode, not with what the collection holds, and W3.6 names
  `pad_map` as the one new contract.
- **A "pad hit" claim per note.** A recording would carry thousands of
  claims saying what lineage spans already say, and reach and destroy would
  not read them.
- **Lineage output on the MIDI Artifact.** The MIDI did not contain the
  sample's sound; the recording did, and the recording is what reach must
  cap.
- **Choosing the pad map by date.** Card loads are not recorded, and a
  wrong choice narrows reach silently; the operator names it, as joining a
  session is the operator's act (ADR 0107).
- **Velocity as gain.** A sampler's velocity curve is device-specific;
  stating a gain the proposer cannot know would be invented evidence.

## New nouns

- **Pad map** (`archive.claim/pad_map`). (1) It says which pad of a
  sampler bank holds which sample and what MIDI fires it, which neither a
  slot (a position name) nor a MIDI stream (events without meaning) says.
  (2) No verb; a person or a bank grammar asserts it. (3) None itself; it
  lets the proposer's lineage name its evidence. (4) It is code inside an
  existing concept: a claim contract about a `collection`. (5) No; facts
  are claims. (6) A claim contract (W3.6).
- **Pad**, **bank**, **trigger** and **play** are fields of the pad map,
  not nouns.
- **Pad proposer** is a derivation run, as ADR 0100's path proposer is;
  not a new noun.

## Touch points

Verified to exist: `capture/capture.py` (`main` gains the `padmap`
subcommand); `enrich/proposers.py` and `enrich/probe.py` (read for the
sample length); `world/documents.py` (`_pins`, unchanged: no pad map
edge); `world/judgments.py` (`standing`, for the alignment choice);
`world/world.py` (`_project_review_claim`, the pad map); `world/circle.py`
(`MEMBER_CONTRACTS`, unchanged: the pad map stays out);
`world/contract_graphs.py` and `world/projection/6/vocabulary.ttl` (the
pad map graph and its `arch:` terms); `door/src/Archive/Door/Contracts.hs`
(the contract bundle).

New, or new in an accepted but unbuilt record: `enrich/pad_lineage.py`
(this record); `capture/bank_grammar.py` and `capture/library.py`
(ADR 0100; a bank grammar may propose a pad map); `enrich/align.py` and
`enrich/midi_notes.py` (ADR 0107); `world/collections.py` (ADR 0100;
`check_pad_map` beside `check_collection_members`); `spec/archive-spec.md`
and `spec/contracts/` (the `pad_map` bundle and its profile rules);
`docs/vocabulary.md` (*pad map*) when accepted.

## Open for the operator

1. **Pad notes in the notes proposer.** ADR 0107's `midi_notes` reads a
   pad struck on channel 1 as a pitch. Recommended: when its run names a
   pad map, notes on a pad trigger become `unpitched` with `part` the bank
   and pad, except key-range pads, which stay pitched; an amendment to
   ADR 0107.
2. **Run by hand or in the chain.** Recommended: by the operator's command
   per session only, because it can narrow the reach of a recording others
   already read; never automatically in the enrichment chain.
3. **Member-capacity pad maps.** A friend who owns the sampler knows its
   map best. Recommended: not now; pad maps stay the operator's and the
   bank grammar's, as collections and membership do (ADR 0100, Settled 2).
4. **Live pad display at the node.** Recommended: the node may show which
   sample a pad fired as advice, reading the pad map from World before the
   rehearsal (an addition to [ADR 0109](0109-rehearsal-node.md)'s read
   side); nothing is written to the outbox from it (ADR 0107: live is
   advice).
5. **Skipped notes.** Notes with no known bank or pad are counted but not
   recorded. Recommended: the run's report lists the counts; no claim
   records "unknown sample here", since whole-output lineage with no input
   cannot be expressed and an absent claim already says nothing.
