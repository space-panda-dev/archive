# 0106. A set list is a claim of intent, and a clock asserts its own meter

Status: proposed (wave 2, drafted 2026-10-19; open questions settled 2026-10-19)

Two things a band brings into a rehearsal say what was meant to happen, and
nothing in an archive can hold either.

- **The plan.** A band rehearses from a set list: these songs, in this
  order, this one at 92, that one in B flat, this one sung by the drummer.
  A rehearsal is a `recording_session` Entity whose parts
  `archive.claim/session_parts` lists ([ADR 0048](0048-review-app-write-contracts.md)),
  and who took part is a contribution at session scope
  ([ADR 0095](0095-participation.md)). Neither says what the band meant to
  play. So no view can say "we skipped the third song", "we played an
  unplanned one", or "that song ran slow", and the whole-recording matcher
  ([ADR 0072](0072-windowed-work-matches.md)) searches every Work in its
  corpus with no hint of which songs were planned.
- **The clock.** A band that plays to a click, or whose drum machine or
  sequencer sends MIDI clock, has a tempo map that no person and no beat
  tracker asserted: the clock's own. Meter is a timeline of layers, each
  asserted by someone ([ADR 0077](0077-chord-stacks-and-meter.md)), and a
  beat tracker's beats are a beat-only layer. Nothing says who asserts a
  click's beats, which recording they count, or how they stand beside the
  beats detected in the audio.

The songbook and the review app both want the plan, and the live runtime of
wave 3 (live song guess, tempo hints) will want the plan and the clock. Wave 2
drafts only the evidence contracts that runtime will read.

The operator decided the following on 2026-10-19 (wave 2 decisions W2.12,
and the earlier settled decisions this wave turns into contracts); this
record states them as given.

- **Set lists.** A set list is a claim of intent on a session; live song ID
  may use it as a prior; the batch matcher records any prior it used in its
  run parameters and runs without one by default.
- **W2.12 Set list entries** are an ordered list of Works, each optionally
  with a target tempo, a key and who sings; revisions record live edits.
  Target tempo powers "that song ran slow".
- **Clocks.** A click's or MIDI clock's tempo map is a meter claim asserted
  by the clock (a process agent) beside the beats detected in the audio
  (ADR 0077 layers).
- **Live is advice, batch is evidence.** Live signals are operational and
  never admitted; the admitted recording is re-analysed in batch.
- **MIDI recorded during a session** is a capture source aligned by capture
  alignment (exact on a shared clock), not a document. Its contract is
  [ADR 0107](0107-multi-device-and-live.md)'s.
- **W2.13 Live responses are ordinary claims** asserted by the operator,
  written `by` the node software, and anchored to the node's recording.

The operator settled this record's open questions on 2026-10-19 (round 6
answer R6.3; R6.16 settles the rest as recommended). The decision below
states the settled choices; the list at the end records them.

## Decision

### `archive.claim/set_list` version 1

A **set list** says what one person intended a session to hold: Works, in
order, each optionally with a target tempo, a key and its singers. It maps to
PROV's `prov:Plan` (a set of steps an agent intends), as a mapping only
([ADR 0012](0012-inherited-meaning.md)). Its references, each an exact
revision:

- at most one `session`, an Entity revision of kind `recording_session`.
  A set list written before the rehearsal has none, because the session
  Entity's identity comes from its first part's content (ADR 0048) and does
  not exist yet; the revision made after the session is admitted pins it
  (Settled 1);
- 1 to 200 `work` references, distinct `musical_work` Entity revisions;
- 0 to 32 `singer` references, distinct `person` Entity revisions;
- at most one `during`, an `archive.claim/audio_selection` Claim revision
  (version 2, or ADR 0099's version 3 once installed) whose track spans the
  rehearsal node's parts: on a revision made live at a rehearsal, where in
  the node's recording the edit was made. It is how
  [ADR 0107](0107-multi-device-and-live.md) anchors every live response,
  so a set-list edit is anchored like a cut or a title; there is no
  `anchor` role and no `anchor_sample` field (R6.3).

Its payload:

```json
{
  "label": "First set",
  "entries": [
    {"work": 0, "tempo": {"bpm": "92", "beat": {"denominator": 4}},
     "key": {"collection": [0, 2, 3, 5, 7, 9, 10], "tonic": 10, "mode": "major", "text": "Bb"},
     "singers": [0]},
    {"work": 1},
    {"work": 0, "tempo": {"bpm": "100"}, "singers": [0, 1]}
  ]
}
```

- `entries`: 1 to 200, in the intended order. An entry's `work` is a
  0-based position among the `work` references; a Work may appear in several
  entries (a song played twice), so entries index the references rather than
  repeat them. Every `work` reference is named by at least one entry.
- `tempo`, optional, the **target tempo**: `bpm`, exact decimal text from 1
  to 999 with at most three places (as weights are written, so canonical JSON
  has one spelling), and optional `beat`, the note value counted (a
  `denominator` and optional `dots`, 0 to 2, as a modulation's note values
  are, ADR 0077). Without `beat` it is beats per minute of whatever beat the
  band counts. It is an intention, never a measurement: the played tempo
  stays derived from meter claims, never stored (ADR 0077).
- `key`, optional: one key reading in [ADR 0080](0080-key-and-tuning.md)'s
  shape (a key, `no_key`, or text only), with no weight.
- `singers`, optional: 1 to 32 distinct positions among the `singer`
  references. Absent says nothing about who sings; it never means
  instrumental. Every `singer` reference is named by at least one entry.
- `label`, optional, 1 to 200 characters: the set's own name, so a session
  with two sets has two set lists.

**Identity and revisions.** A set list's identity name is `set-list-`
followed by a lowercase UUIDv4, in namespace `archive.audio/set_list`; every
set list is a new object. An edit, before the rehearsal or live during it, is
a revision of the same object, which keeps its agent: a bandmate who would
play it differently asserts a set list of their own, and the two stand side
by side (INV-7). Revisions are compared before writing, so a resend of an
unchanged list writes nothing. When a live edit happened is its `during`
selection's span in the node's recording, mapped onto the session's other
sources by capture alignment (ADR 0107's `aligned_spans`), not the
envelope's `at`, which is Door's commit time (Settled 2).

**Door rules** (a specification release adds them with the bundle): the
envelope `agent` is a `person` Entity (`x-archive-agent`); a revision keeps
its agent (`x-archive-supersession`); reference targets by kind (`kinds`,
ADR 0048); distinct `work` and distinct `singer` references
(`x-archive-ordered-distinct-refs`); entry and singer positions within their
roles (`x-archive-ref-index`); a `during` target that is an installed
selection version; the decimal pattern and ranges; a key reading by
ADR 0080's rules. One rule is new: every reference in a role is named by
some position the path reaches. It is `x-archive-ref-index` with
`covering: true`, a keyword extension Door does not yet evaluate.

**Who may assert it** ([ADR 0094](0094-peoples-writes.md)). A set list is a
claim about readable things, so it falls under `annotate`: a member may
assert one naming themselves as agent, and the relay's existing rule that
every pin be readable applies to the session, the Works and the singers.
The live edits the rehearsal node sends are the operator's, `by` the node
software (W2.13); they travel as a response group of the node's outbox,
pinning the group's selection as `during`, and are flushed after the node's
recording is admitted (ADR 0107, R6.3). Because a revision keeps its agent
(only its agent may revise it, Settled 3), the operator can revise live only
a set list the operator asserted. The set list under `annotate` is part of
the one ADR 0094 amendment that lists every wave-2 ability addition
(R6.10).

### A clock asserts its own meter

A **clock** is whatever kept time at a rehearsal without being a person: a
metronome on the rehearsal node, a click in someone's ears, a drum machine
or sequencer sending MIDI clock. It is an Entity of kind `process`, an agent
the core context already defines (D-8), labelled in words ("Node
metronome", "Drum machine clock"), never a person's name. Its identity name
is `clock-` followed by a lowercase UUIDv4, in namespace
`archive.audio/clock`, and one clock Entity is reused for every session that
clock keeps time at. Entities stay the operator's to propose (ADR 0094).

**A clock's claim is an ordinary timed meter claim.** It is an
`archive.claim/meter_layers` version 2 claim (timed, ADR 0083), with no
contract change:

- envelope `agent`: the clock's `process` Entity; `by`: the tool that read
  the clock;
- `source` and `stream`: the recording whose samples it counts, chosen among
  the recordings made on the clock's own sample clock, so the claim is exact
  and sits on the same source as the beat tracker's claims when it can: the
  session's mix when the click was a channel of the same multitrack, the
  node's recording when the node received the MIDI clock or made the click;
- `analysed`: the click channel's Artifact when the click was recorded as
  audio on the same multitrack, which shares the source's timeline exactly
  as a stem does (ADR 0077's mix-first rule is kept: the clock's claim is
  another claim about the same source);
- `run`: the derivation run that read the clock ([ADR 0064](0064-derivation-runs.md)),
  whose `used` pins the clock's capture (the click channel, or the MIDI
  capture of ADR 0107) and any capture alignment it relied on, and whose
  parameters say how ticks became samples.

What the clock states, and no more:

- **One beat-only layer** labelled with the clock's name, with no `role`: a
  click asserts neither the meter as written nor as felt, only its own, and a
  band playing in three against a four-click is two claims that differ.
  Beats exist only while the clock ran (MIDI Start to Stop, the click on);
  a gap asserts nothing.
- **`beat_unit`** only when the clock states it: MIDI clock counts 24 ticks
  per quarter note, so its layer's `beat_unit` is a quarter; an audio click
  of unknown setting has none.
- **`downbeats`** only when the clock marks them (an accented click, a
  sequencer's bar position); never inferred.
- **A bars layer** only when the clock's own configuration states a time
  signature (a metronome set to 7/8 grouped 2+2+3, a sequencer's time
  signature events), recorded in the run's parameters; otherwise the clock
  never invents a bar (ADR 0077's amendment).

**Beside the detected beats.** The clock's claim and a beat tracker's are two
claims over the same source, attributed to different agents, and neither
supersedes the other (INV-7). In [ADR 0095](0095-participation.md)'s tiers a
clock is `machine`. Pairing the clock's beats with a tracker's or a chart's
bars is the open question of matching beat grids; this record does not
answer it, and World shows both. A clock's tempo map is evidence of what the
clock did, not of what the band played: the band's tempo is still derived
from whichever meter claim a view reads.

**What is not a clock claim.** A live tempo hint from the node is advice and
never admitted. A person's "4 bars at 92" is that person's meter claim, as
W2.2 says for samples. Tempo is never a stored field on a clock claim; it is
derived from the clock's beats like any other layer's (`beat_tempi` in
`world/harmony.py`).

### The batch matcher records any prior

`enrich match-windows` runs without a prior by default, and its parameters
and runs are then exactly today's, so an equivalent rerun still writes
nothing (INV-5). With `--prior <set list>`, naming one set list revision that
pins the recording's session:

- the run pins that revision in `used`, and its parameters Artifact gains
  `"prior": {"set_list": "<revision id>", "effect": "candidate_works"}`,
  so a run with a prior is a different run, and a reader sees which list
  steered it;
- the effect is only to widen what is compared: the set list's Works join
  every region's candidate Works and every window's comparison (ADR 0072's
  steps 3 and 5) beyond their caps. The corpus, the distances, the hit,
  suggestion and near-tie limits are unchanged, so a prior can make a
  planned song be compared, never make a weaker match be suggested, and an
  unplanned song is found as before (Settled 4);
- a prior run proposes no claim an earlier run already made for the same
  window and Work.

The operator names the set list; the matcher never picks one. Live song ID
may use a set list more strongly (the next entry as the likeliest song); that
is wave 3's, and live guesses are never admitted.

### World and the relay

- **Projection.** World projects a set list as `prov:Plan` and `arch:SetList`,
  with `arch:intended_for` its session, each entry a resource of its own
  (`arch:SetListEntry`) with `arch:position`, `arch:entry_work`,
  `arch:target_bpm`, `arch:target_beat`, its key reading projected as a key
  claim's reading is (ADR 0080), and `arch:sung_by`; a `during` selection
  as `arch:edited_during`. A clock's meter claim is
  projected as any meter claim, its agent an `arch:ProcessAgent`
  ([ADR 0029](0029-core-context-and-bootstrap-contract.md)).
- **`archive.view/set_list_played` version 1.** Its question is "what did
  the set list plan, and what was played?". Its subject is
  `{"session": <object id>}`; its parameters are `set_list` (an object id;
  by default every head set list pinning the session, each answered on its
  own) and `tolerance_percent` (default 3). For each entry it answers the
  occurrences in the session linked to the entry's Work (by any asserter's
  head `performance_work`, with each link's standing; an occurrence is in the
  session when its selection's sources are parts of the session's head
  `session_parts`), in played order; `skipped` entries; `unplanned` Works
  played; and, per played occurrence with a target tempo, each timed meter
  claim over its audio (on the same source, or on another through a capture
  alignment) with its asserter, asserter tier, the median tempo derived over
  the occurrence's span in the target's beat (or beats per minute without
  one), and `ran` (`slow`, `fast`, or `on`, by the tolerance). It chooses no
  meter claim: no current rule for meter exists
  ([open](../open-questions.md#claims-and-provenance)), so every reading is
  listed, and a clock's reading is marked as the clock's. `withheld` is as
  every view states it.
- **Relay.** The relay accepts set list drafts under `annotate`, serves set
  lists within the caller's audiences ([ADR 0093](0093-audiences.md)) like
  any claim, and serves the view from the registry. A member cannot draft a
  clock's claim: a member draft names the caller as agent, and the clock is
  not a login. No new route and no new refusal code.

## Consequences

- The songbook can show a rehearsal's plan beside what was played, and
  "that song ran slow" is a view: the target is a claim, the played tempo is
  derived from the meter claims over the occurrence, and the view says
  whose.
- A plan written days before can be revised live at the rehearsal and kept
  afterwards; every revision is kept, so the history of a set list is the
  history of the night's changes.
- A matcher run says whether a set list steered it and which one. A run
  without a prior is exactly today's run.
- A click or MIDI clock is evidence beside the beat tracker, never a
  replacement for it, and its exact beats are on the record for wave 3 and
  for any later beat-grid matching.
- A target tempo is stored where tempo was never stored before. It is a
  stated intention, not a reading of audio, so ADR 0077's rule (tempo
  derived from bars and beats) still holds for every meter claim; the
  vocabulary keeps "tempo is never stored" for meter claims and adds that a
  target tempo is a stated intention, not a tempo of audio (Settled 6).
- No invariant changes. INV-7 holds because two set lists, and a clock's
  layer beside a tracker's, coexist; INV-5 because a matcher without a prior
  writes as before.

## Alternatives considered

- **Set list as a session payload or as part of `session_parts`.** An
  Entity holds only `kind` and `label` (D-8), and parts are what was
  recorded, not what was meant; a plan exists before any part does.
- **Set list as a document** (a text file of titles). Titles never identify
  a Work (ADR 0042), and a target tempo or singer in free text cannot drive a
  view or a prior.
- **One claim per entry.** The order is the plan; splitting it loses that
  the entries were asserted together, as ADR 0077 rejected one claim per
  meter layer.
- **Work references repeated per entry.** A song played twice would pin one
  revision twice, which distinct references refuse; positions keep the
  references distinct.
- **A target tempo as an untimed meter claim.** Untimed meter is a Work's
  written bars (ADR 0083); a target is a session's intention for one entry,
  and has no bars.
- **The clock's claim asserted by the reading tool.** The tool only
  translated ticks into samples; the tempo map is the clock's, so the clock
  is the agent and the tool is `by` and the run.
- **A new clock contract, or a `role` of `clock`.** ADR 0077's beat-only
  layer already says beats without meter; a role would rank layers by kind,
  which INV-8 refuses, and the agent already says what kind of asserter it
  is.
- **The prior narrowing the corpus to the set list.** An unplanned song
  would then go unrecognised, and a run would suggest what it was told to
  expect: advice treated as evidence.

## New nouns

- **Set list** (a claim contract). (1) It says what a person intended a
  session to hold, which no claim says; `session_parts` says what was
  recorded and a contribution who took part. (2) No verb; it is asserted,
  revised and judged like any claim. (3) None itself; it keeps INV-7 by
  coexisting with other plans, and lets a matcher keep INV-5 by naming its
  prior. (4) It is code inside an existing concept: a claim contract read by
  World's views and the matcher. (5) Its claims are evidence of intent; the
  comparison with what was played is computed. (6) A contract, not a
  subsystem.
- **Set list entry.** (1) One planned performance: a Work and its targets.
  (2) No verb. (3) None. (4) A field of the set list. (5) No. (6) A detail.
- **Target tempo.** (1) The tempo intended for an entry, which no meter
  claim states. (2) No verb. (3) None. (4) A field of an entry. (5) No.
  (6) A detail; it is not *tempo*, which stays derived.
- **Clock.** (1) It names the non-person agent that kept time at a session.
  (2) No verb; its claims are asserted like any agent's. (3) None. (4) It is
  an Entity of the existing kind `process`, not a new kind. (5) No. (6) A
  word for an existing agent kind, not a subsystem.
- **Prior** (of a matcher run). (1) It names the set list a run used to
  widen its candidates. (2) No verb. (3) It lets a run keep ADR 0006's
  rule that configuration is recorded. (4) It is a run parameter. (5) No.
  (6) A detail of the matcher's parameters.

## Touch points

- `world/set_lists.py` (new): `check_set_list` (references, entries,
  positions, covering references, the decimal and the note value), reusing
  `world/tonality.py`'s `check_key_reading` and `world/harmony.py`'s
  `note_value`; the `set_list_played` answer.
- `world/harmony.py`: `beat_tempi` and `bar_tempi`, read by the view;
  `check_meter_layers` and `world/form.py`'s `check_meter_layers_v2`
  unchanged for clock claims.
- `world/record_views.py`: `VIEWS` and `HANDLERS` register
  `archive.view/set_list_played` version 1.
- `world/contract_graphs.py`, `world/world.py` (`PROJECTION_REVISIONS`) and
  `world/projection/6/vocabulary.ttl`: the set list graph and its `arch:`
  terms.
- `world/circle.py`: `MEMBER_CONTRACTS` gains set list version 1, and
  `world/grants.py` (new, ADR 0094) maps it to `annotate`.
- `world/participation.py` (new, ADR 0095): `asserter_tier` places a
  `process` agent as `machine`, already its rule.
- `enrich/enrich.py`: `run_match_windows` (`--prior`, the `used` pin, the
  duplicate check); `enrich/windows.py`: `regions_of` and `claim_rounds`
  take the prior's Works as extra candidates; `enrich/cover.py`:
  `parameters` adds `prior` only when one is given.
- `enrich/clock.py` (new): reading MIDI clock ticks and click onsets into a
  timed meter claim with its run, and the clock Entity.
- `capture/capture.py`: proposing a clock's claim when a session's capture
  includes a click channel or a MIDI clock (with ADR 0107's MIDI source).
- `spec/archive-spec.md`: the set list bundle and the covering
  `x-archive-ref-index` rule; `docs/vocabulary.md`: *set list*, *set list
  entry*, *target tempo*, *clock*, *prior*, and the tempo line (Settled 6).
- ADR 0107's outbox: set-list edits as a response group (R6.3).

## Settled by the operator (2026-10-19)

1. **A set list before its session exists.** `session` is optional; the
   first revision after the session is admitted pins it (R6.16).
2. **When a live edit happened.** A live revision pins an
   `audio_selection` over the node's parts in its `during` role, as ADR 0107
   anchors every response; set-list edits join ADR 0107's outbox response
   groups; no `anchor` role or `anchor_sample` (R6.3).
3. **Who may revise a set list.** Only its agent; another person's plan is
   their own set list (R6.16).
4. **What a prior may do.** Only widen candidates (`candidate_works`)
   (R6.16).
5. **The view's tolerance.** 3% by default, a view parameter; every meter
   claim listed (R6.16).
6. **The vocabulary's "tempo is never stored".** Kept for meter claims; a
   target tempo is a stated intention (R6.16).
7. **One clock Entity per device or per session.** Per device or program,
   reused across sessions (R6.16).
