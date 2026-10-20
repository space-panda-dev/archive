# 0112. Bulk import at scale: song spans eagerly, the rest on demand, and eager everything as a fast mode

Status: accepted (operator, 2026-10-19) for the modes, the stand-in stems,
the scale-out without rented GPUs, and keeping `chords-jiang`; the
mechanics the operator did not state are listed under
[For the operator to confirm](#for-the-operator-to-confirm).

## Context

The feature bank ([ADR 0073](0073-feature-bank.md)) is filled today by
hand-written scripts that run `extract.py run` over one recording after
another, one extractor at a time, each with up to four threads. The backfill
profile (2026-10-19, on copies; no shadow data was written) measured what
that costs on the x86-64 laptop, which holds the bank:

- **About one second of compute per second of audio** (0.98 on a
  2715 s rehearsal, 1.09 on a 554 s one). Ten thousand hours of recordings,
  a few decades of a working band, would take 409 days.
- **Where it goes.** `stems` 59%, `f0-pesto` 13% (twice: vocals and bass),
  `melody` 8% (twice: its vocal-stem pass falls short of its voiced
  fraction, so it measures the mix too, and the mix pass is the costlier),
  `chords-jiang` 6%, `notes-basic-pitch` 5%, the rest 9%.
- **What is not a lever.** Process start and model loading are about 1%;
  decoding the same audio ten times over is 1.7%. Inference is over 90%.
- **Parallel copies beat threads.** On the laptop's eight cores, several
  copies with one or two threads each give more throughput than one copy
  with many: 1.57x for `stems` (four copies of two threads against one of
  four), 2.76x for `chords-jiang`, 1.97x for `f0-pesto`, 5.3-5.7x for
  `rhythm`, `chords-chordino` and `melody`.
- **The Mac's GPU separates at 0.10 s per audio-second**, 5.5 times the
  laptop, with stems that agree downstream within the tolerance two CPUs
  already need ([ADR 0090, amendment](0090-extractor-runs-travel-through-spool.md#amendment-2026-10-19-a-stem-is-a-stem)).
- **Songs are about half the audio.** Curated song occurrences cover 12.0
  of the shadow's 22.8 hours (52.6%); four recordings have none.
- **Projected:** parallel copies and the GPU together, 7.0x (58 days per
  10,000 hours); with only song spans made eagerly, 12.7x (32 days). After
  both, the laptop is the bottleneck again, and its largest remaining item
  is `f0-pesto`.

Every later step of the implementation order (lineage, samples and the
index, people, voice and ASR, live) adds extractors or readers of the bank,
so this comes first.

## Decision

### Three modes, one desire

Which recordings are enriched with what, and how eagerly, is a **derivation
desire** of policy ([ADR 0007](0007-policy.md)); the scheduler below is
mechanism. Until the policy language exists, the mode is the scheduler's
configuration. The modes:

- **`songs`** (the default): every extractor runs eagerly over the whole of
  every recording, except the **expensive** ones, which run eagerly only
  over the recording's song spans; the rest of the recording is made on
  demand.
- **`eager`**: every extractor over everything. Some operators want all of
  it, made once; it is a supported mode, measured and kept fast, not a
  fallback.
- **`on-demand`**: nothing is made until it is asked for.

In every mode, a request is made on demand (below), and what an index
reads is made eagerly over what that index covers (below).

**Expensive is measured, not named.** An extractor is expensive when its
recorded cost on the bank's machine is above a threshold (proposed: 0.04
CPU-seconds per audio-second). By today's numbers that is `stems`,
`f0-pesto`, `melody`, `chords-jiang` and `notes-basic-pitch`; `frames`,
`tonal`, `rhythm`, `chords-chordino`, `structure`, `loudness-r128`,
`quality` and `colour` stay whole, and the span detector and work matching
need them whole anyway.

### Song spans become region entries

**A run may be limited to a region of its content**: `[start, end)` in
samples at the content's stream rate (the probe's stream facts,
[ADR 0065](0065-audio-stream-facts.md)). The region is part of what decides the
outputs, so it is part of the key: a region entry's key is the digest of a
description under a new contract, `archive-feature-bank-key/2`, which is
`archive-feature-bank-key/1`'s members and `region` (`start`, `end`,
`rate`). A whole-content key stays a `/1` key, unchanged, so nothing
already in a bank moves. The validator for `/2` keys and region entries
lands in the same change as the first writer.

**The runner cuts; programs do not change.** The runner decodes the region
to a FLAC cut with pinned arguments, runs the program over the cut as over
any content, and records in the entry the region and the cut's digest.
Outputs are on the cut's timeline; a reader adds `start`. Region entries
are filed under the content's directory, so deleting the content deletes
them ([ADR 0075](0075-stems-as-content.md)). An extractor's needs are made
over the same region. Through Spool the cut travels, not the recording,
and the bank checks the result by cutting again and comparing digests.

**Which regions.** A recording's song spans come from the first of these
sources that has any for it, and a source that has some wins whole, never
mixed with a lower one:

1. the pieces on the recording of the selections head song occurrences pin
   (curators said these are songs);
2. the pieces of head selections on the recording (a curator marked them,
   though no occurrence says they are songs);
3. the span detector's `music` suggestions
   ([ADR 0066](0066-span-suggestions.md), [ADR 0091](0091-talk-needs-level.md));
4. the `structure` extractor's `playing` spans, when the bank holds them.

A recording with none has no song spans, and its expensive features are made
only on request. Each span is widened by a margin on both sides and snapped
outward to whole seconds, and regions that overlap or nearly touch are
merged. A span edited within its margin is still covered, so curation does
not rerun anything. (`enrich/regions.py`; the precedence is item 3 of the
list for the operator to confirm.)

**A reader takes whatever covers its span.** The whole-content entry covers
every span; otherwise any region entry whose region contains the span. A
span that nothing covers is a request (below). Region entries are never
stitched into a whole one.

### On demand: the first request queues it

**A request is a file, named by the key it asks for.** When a reader finds
no entry that covers what it needs, it writes a **bank request**
(`archive-feature-bank-request/1`: the content digest, the extractor, the
region or `null`, the feature it measures if a preferred input, and who
asked) into the bank's `requests/` directory under the key's name. Asking
again is the same file, so a request is idempotent. Requests are
operational state, deletable like the rest of the bank (INV-4).

**An incomplete read says so** (INV-24). A relay read whose features are
not made answers that they are queued, never an empty result: practice
audio's `stems_not_made` refusal ([ADR 0089](0089-practice-audio-and-exports.md))
becomes a `queued` status when the bank has a scheduler, and stays the
refusal when it has none; likewise a bank feature's file answers
`feature_queued` (HTTP 202) instead of `feature_not_made`, and the bank
listing names what it queued with the key each entry will have, which a
practice request may name. `enrich propose` and `enrich/live.py compare`
([ADR 0108](0108-live-analysis.md)) write requests for what they lack and
say which.

### The scheduler: `enrich backfill`

One command replaces the hand-written scripts:

```sh
enrich/enrich.py backfill --archive <dir> --bank <dir> --config <file>
    [--mode songs|eager|on-demand] [--cores n] [--spool "<spool> --dir <dir>"]
    [--follow] [--dry-run]
```

- **It plans from the archive and the bank.** For every Artifact with an
  audio stream claim (as `enrich propose --all` finds them), it computes the
  keys the mode asks for (whole or region), drops those the bank holds or
  can stand in for ([ADR 0090, amendment](0090-extractor-runs-travel-through-spool.md#amendment-2026-10-19-a-stem-is-a-stem)),
  and orders the rest: requests first, then song regions, then everything
  else. The bank is the ledger; the scheduler keeps no state of its own.
- **Parallel copies sized to the machine.** It runs many recordings at once,
  each extractor in copies of one or two threads (set per extractor in the
  configuration, with its thread variables), as many as the cores allow and
  bounded by memory per copy; never one copy with many threads.
- **Fan-out through Spool.** Extractors the configuration routes to a worker
  (first: `stems` on the Mac's GPU) are put through `enrich/remote.py`
  (ADR 0090) and collected as results arrive; their dependents wait for the
  collect. When the worker is away, the configuration says whether the
  laptop separates instead, since a CPU stem stands in just as well. The
  Mac's small disk bounds the worker to one recording in flight.
- **Resumable and idempotent.** Each run is the runner's own (lock, check,
  rename); a killed backfill loses at most the runs in flight, and a rerun
  over an unchanged archive and bank makes and writes nothing (INV-5). A
  failure is recorded beside where the entry would be, as today, and is not
  retried until asked.
- **`--follow`** keeps it running: it watches `requests/` and new
  admissions and makes them as they arrive.
- **`--dry-run`** prints the plan and its projected cost from the recorded
  costs (below).
- On the laptop it runs as a low-priority service (nice, idle I/O class).

### Cost is a gated metric

**Every extractor has a recorded cost.** A `bench-cost` suite, beside
`bench-horn` ([`enrich/bench`](../../enrich/bench/README.md)), runs each
extractor over a fixed private set on the x86-64 laptop and records
CPU-seconds (user and system, the process group's) per audio-second at its
configured thread count, and wall time beside it. A baseline is recorded per
extractor, version and machine. **The gate:** the suite fails when an
extractor's CPU-seconds per audio-second rise more than a tolerance above
its baseline (proposed 15%). CPU time is gated and wall time is not, since
the machine's load moves wall time (the bench's existing rule). GPU runs
are recorded, not gated.

**The backfill measures itself.** Each backfill reports audio-seconds made
per wall-second, per mode, and the projected days for the archive's
remaining audio; this is the number the `eager` mode is pushed on.

**Accuracy and cost gate together.** A change that makes an extractor
cheaper (a cheap decision before `melody`'s mix pass, a model on the GPU)
must keep its accuracy gate (the bench's F1, the melody metrics, the
tolerance of the amendment) and pass the cost gate; the backlog's
experiment floor gets its cost axis from this.

### Consistent with the records it touches

- **[ADR 0074](0074-extractor-contract.md), the extractor contract.**
  Programs are unchanged: a region is the runner's cut, and
  `interchangeable` is a manifest declaration that changes no output. A
  version still changes whenever outputs can.
- **[ADR 0090](0090-extractor-runs-travel-through-spool.md).** Tasks stay
  one entry each; a region task's payload (`archive-extractor-task/2`) adds
  the region and the cut's digest, and a worker that knows only `/1`
  refuses it for good (exit 65). Only workers in the operator's custody
  (INV-25).
- **[ADR 0102](0102-retrieval-index.md), the retrieval index.** The index
  never runs an extractor, so whatever it embeds (selections and Artifacts
  up to 30 s in index version 1) has its `timbre-<model>` entry made
  eagerly in every mode, and the index names units it found no entry for,
  rather than leaving them out silently (INV-24).
- **[ADR 0104](0104-transcription-and-speakers.md), local ASR.**
  Unchanged: `asr-whisper` runs on whole content, and which content is
  policy (its Settled 11). It is not run over song regions; under `songs`,
  voice notes are made eagerly and rehearsal recordings on demand.
- **[ADR 0108](0108-live-analysis.md), streaming mode.** The scheduler runs
  batch only; stream events are never filed. A batch program that runs its
  streaming core is held to the same cost gate, so a streaming refactor
  that slows the backfill fails it. A node's recording, once admitted, is
  enriched like any other.

### Out of scope

- **Rented cloud GPUs.** Not ruled out, but not built or planned in this
  round; every worker is the operator's own.
- **Another chord model.** `chords-jiang` stays (its MIREX was the best of
  four in the spike, and chords are 9% of the cost); it is made faster by
  parallel copies, and `chords-chordino` replaces it only if numbers say so.
- **A lighter separator.** None was found that is faster on the CPU; the GPU
  is the lever.
- **Decode sharing and process reuse.** Measured at under 3% together.
- **Windowed embeddings of long recordings**, which ADR 0102 defers.

## Consequences

- The projected 10,000-hour backfill falls from about 409 days of laptop
  time to about 32 in `songs` mode and 58 in `eager` mode, before the
  cheaper `melody` and `f0-pesto` on the GPU.
- A reader of the bank must accept a region entry and a stand-in stem, and
  must say "queued" where it used to say nothing; every reader that goes
  through `Bank` gets this from one place.
- The bank grows a `requests/` directory, region entries, and equivalence
  records; all are regenerable.
- Spool 0.0.4 must be deployed on both machines with the GPU grant (already
  in the backlog's operations).

## Alternatives considered

- **Song spans first, the rest in idle time.** The operator chose on demand
  for the rest; eager everything is its own mode.
- **Spans as admitted cuts** (each song span an Artifact of its own). It
  would admit derived audio to save compute, and a re-curated span would be
  a new Artifact; a region entry is regenerable and keyed by the span.
- **Fixed tiles of the recording** (a minute each) instead of song regions.
  Stable keys under curation, but every tile edge cuts a separator's or a
  tracker's context, and a song is many tiles.
- **One copy with all eight threads.** Measured 1.3-5.7x slower per
  extractor than several narrow copies.
- **A queue in Spool for on-demand requests.** Spool queues work for
  workers; a request names a key the scheduler may satisfy from the bank or
  a stand-in without running anything, so it stays bank state.

## New nouns

- **Region entry**: a bank entry over `[start, end)` of its content, keyed
  by `archive-feature-bank-key/2`.
- **Bank request**: a file in `requests/` asking for one key; made by a
  reader, made good by the scheduler.
- **Equivalence record**: the measured agreement that lets one entry stand
  in for another (ADR 0090's amendment).
- **Expensive extractor**: one whose recorded cost is above the threshold.

## Touch points

- `enrich/backfill.py` (new) and the `backfill` command in `enrich/enrich.py`.
- `enrich/bank.py`: region keys and entries, stand-in lookup, requests,
  equivalence records; `enrich/extract.py`: `--region`, `equivalence`,
  `--exact`; `enrich/remote.py`: task `/2`, routing to stand-ins.
- `enrich/extractors/stems/extractor.json` (`interchangeable`), and
  `f0-pesto` if it gains a device.
- `relay/practice.py` and `relay/openapi.json` (`queued`).
- `enrich/bench`: the `bench-cost` suite.
- `docs/feature-bank.md`, `enrich/README.md`, `docs/shadow-runbook.md`.

## For the operator to confirm

1. The expensive threshold, and that it is measured on the bank's machine:
   proposed 0.04 CPU-seconds per audio-second, raised to 0.05 by the
   [amendment](#amendment-2026-10-20-usable-first-the-rest-in-the-background)
   once the cost gate measured `chords-chordino` at 0.042.
2. The region margin and merge gap: proposed, 10 s each side and merging
   regions less than 30 s apart.
3. The precedence of song spans: occurrences, then plain selections, then
   the span detector's `music` suggestions, then `structure`'s `playing`
   spans, the first source with any winning whole (the profile: occurrences
   cover 52.6% of the shadow's audio, any selection 68.5%, "playing" 92%,
   which saves little; triage reaches about half only if detection matches
   curation).
4. Region keys as a new key contract `/2`, leaving every `/1` key as it is.
5. The cost gate's tolerance (15% on CPU-seconds per audio-second) and that
   wall time and GPU time are recorded, not gated. As built
   (`enrich/bench/cost.py`), the sum is gated at 20% and each unit at
   double its baseline plus 0.02, since two runs on one machine moved the
   sum by 10% and one cheap unit by 2.2x.
6. Under `songs`, `asr-whisper` eager for voice notes and on demand for
   rehearsals.
7. When the GPU worker is away, the laptop separates (proposed default:
   yes in `eager`, no in `songs`). Superseded by the amendment: every mode
   has a limit, and free cores here take routed work in every mode.

## Amendment (2026-10-20): usable first, the rest in the background

Status: accepted (operator, 2026-10-20) for the direction: change what is
waited for, a fast pass over everything first, stems and what measures them
in the background by priority, anything opened ahead of all of it, and
time-to-usable reported next to time-to-done. The numbers and the exact
line of "usable" are the operator's to confirm (below).

**Why.** Measured in the first round, `--eager` on the laptop alone runs at
0.337 seconds per audio-second saturated, so the 23-hour shadow takes 6-8
hours before anything new can be read; three quarters of that is `stems`.
Nothing a reader needs first (chords, sections, key, tempo) waits on a stem.

### Usable

**A recording is usable when** it is probed (its `audio_stream` claim is
admitted), the bank holds a whole-content entry (or a stand-in) of every
unit of the **fast tier** for it, and an `enrich propose --all` has run
after the last of them was made, so its chords (`chords-jiang`,
`chords-chordino`), beats and bars (`rhythm`), key and tuning (`tonal`) and
sections (`structure`) are admitted claims; with `--world-store`, World has
been refreshed after that proposal, so they are searchable. Nothing that
measures a stem is part of it. A region entry does not count (no reader
takes one yet); under `songs`, where `chords-jiang` is expensive and made
over song regions, a recording is usable without Jiang's chords.

**The fast tier** is every unit that measures the content itself (no `--from`
feature), prefers no feature, needs only fast units, and whose recorded cost
is at most `fast_above` CPU-seconds per audio-second (proposed 0.2). By the
cost gate's baseline that is `rhythm` (0.032), `tonal` (0.028),
`chords-jiang` (0.151), `chords-chordino` (0.042) and `structure` (0.003);
`frames` comes with what needs it. A configuration may set an extractor's
`tier` outright. Everything else is the **background tier**: `stems`
(1.445), `f0-pesto` on each stem (0.186, 0.174), `melody` (0.076, it prefers
the vocal stem), `tonal` on the bass stem (0.026) and `notes-basic-pitch`
(0.107).

**Time-to-usable** is from the backfill's start until every recording it
found is usable (the `usable` event; `time_to_usable_s` in `done`);
**time-to-done** until everything the mode asks for is made and proposed
(`time_to_done_s`). The dry run projects both from the recorded costs: the
fast tier's CPU-seconds (times a load factor of 1.15, the measured rise
under a full machine) over the cores, and then the background over the cores
and the worker together. Both are the headline numbers, and both are
cost-gated through the per-unit gate.

### Order

1. **Requests** (`requests/`), in either tier, ahead of everything; what a
   request waits for (the stems a measure of a stem needs) is promoted with
   it. A request is removed once its entry is made, or at planning when it
   already is.
2. **The fast tier over every recording**, then `propose --all`. No
   background job starts before that proposal has run.
3. **The background tier by recording**: song recordings first (any song
   region by the precedence above), then the most recently admitted (the
   first `at` of the Artifact's records: the archive holds no recording date
   a scheduler could read), then the rest; within a recording, what others
   wait for first. Proposals run in batches as background units land, at
   most every `propose_every_seconds` (900), and once at the end.

**`--follow`** (needs `--commit`) keeps the pool running: it reads new
requests every `poll_seconds` and plans them first, and looks for new
admissions every `rescan_seconds` (600: probe, plan, fast tier first),
until the unit is stopped or `--follow-seconds` pass. **A request is made
only under its own key.** The scheduler computes the key its configuration
gives what the request asks for, as `bank.ask` computes it; when that is
not the request's key, the asker reads with other parameters, so what the
scheduler would make would never answer it: the request is reported and
kept. The relay's `bank_parameters` must therefore be the backfill
configuration's `parameters` (none on the shadow today).

### The pool

- **A copy is reserved the cores it uses**, not its thread count: a
  `cores` setting beside `threads`. `chords-chordino`'s `threads` parameter
  is 3 and part of its key, but it uses one core (CPU time equals wall time),
  so it is reserved 1; `chords-jiang` (3 threads, 1.7 cores used) 2;
  `notes-basic-pitch` is held to 1 thread and uses 3.4, so it is reserved 3.
  No extractor version changes.
- **The recorded costs are the cost gate's baseline** (2026-10-20), as
  CPU-seconds per audio-second of one pass, in `backfill.SETTINGS` and
  `regions.PROFILED_COSTS`; the threshold for expensive moves to 0.05, between
  `chords-chordino` (0.042) and `melody` (0.076), so the units the span
  detector and matching read stay whole.
- **The worker.** At most `spool_in_flight` (2) routed tasks are with the
  spool at once, chosen in the queue's order, so a request is the next task
  put; with `overflow` (default on), free cores here take routed work the
  worker is not holding. **No mode waits for ever**: a worker that files
  nothing for `local_when_away_seconds` (songs 1800, eager 900, on-demand
  600) plus the time its oldest task should take (`remote_cost` times its
  audio) is absent; its tasks are made here and nothing more is put to it
  for that long.

### Projected on the shadow (22.8 h of audio, the laptop's 8 cores)

From the recorded costs, not measured end to end:

| | fast tier | usable after | background | done after |
|---|---|---|---|---|
| laptop alone | 0.294 core-s per audio-s | about 50 min | 2.32 core-s per audio-s | about 7.4 h |
| laptop, Mac at 0.277 (today) | | about 50 min | | about 4.6 h |
| laptop, Mac at 0.10 (FLAC, two tasks) | | about 50 min | | about 3.0 h |
| `chords-jiang` in the background | 0.121 | about 21 min | | about the same |

**Measured** (2026-10-20, laptop only, `--eager`, the two profiled
recordings, 3269 s of audio, a scratch copy and a fresh bank, no
proposers): usable after 183 s (projected 234 s), done after 3180 s (round
1, without tiers: 2921 s). With two recordings the end is one recording's
`stems` on one two-thread copy, now started after the fast tier, so the
tiers cost 9% of time-to-done there and buy usable 17 times sooner; at the
shadow's scale the background is bound by throughput, not by that tail.

The laptop's background share below the Mac's stems (`f0-pesto` twice,
`notes-basic-pitch`, `melody`) is 0.65 core-s per audio-s, 1.9 h alone on
the shadow: with stems off the laptop, it bounds time-to-done until
`f0-pesto` moves to the GPU.

### For the operator to confirm (amendment)

1. The line of "usable" above, and that `chords-jiang` is in it (about 50
   minutes on the shadow) rather than made first in the background (about
   21 minutes to usable with Chordino's chords): `fast_above` 0.2, or
   `tier: background` for `chords-jiang`.
2. "Most recent" as the Artifact's first admission until a recording date
   exists.
3. The absent-worker limits per mode (1800, 900, 600 s, plus the task's
   expected time), two tasks in flight, and the laptop taking overflow in
   every mode.
4. Background proposals at most every 15 minutes.
5. That the background never starts before the fast tier's proposal, even
   though cores idle while it runs.
