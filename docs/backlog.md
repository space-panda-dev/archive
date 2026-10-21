# Backlog

Known gaps and follow-ups that are not decisions. Decisions waiting for a
person are in [open questions](open-questions.md). Each item says where it was
measured; remove it when it is done, in the change that does it.

The "album trial" is an 18-track studio album run through every extractor and
compared with hand-made charts and tabs; the "studio test" is six studio
recordings of covers already in the archive, run through capture import on a
scratch copy.

## Extractors

- **Downbeat phase.** `rhythm` 4 finds downbeats on 17 of 18 album-trial
  tracks, but often half a bar off: on one track every downbeat falls on beat
  3. `beats-beatthis` lands on beat 1 far more often (19-72% against 0-51%).
  Fix the bar phase, or derive downbeats from Beat This; the 5/4 prior is
  also still to do.
- **Tempo level.** `rhythm` 4 reads three album-trial tracks at the wrong
  level (one at double speed, others at 1.15x and 1.41x); Beat This gets the
  double-speed one right.
- **Key.** `tonal` 5 matches more claimed tonics (17 against 15) but names a
  tonic on fewer tracks (6 against 11), and its confidence is off at both
  ends (tracks at 0.5 or above agree 50% of the time, below 0.5, 42%).
  Weighting the bass on downbeats and endings was tried and did not help.
- **Tuning.** The cross-check of the tuning against the vocal stem is not
  implemented. It needs a minimum voiced share as well as a minimum R: an
  instrumental's vocal stem (3% voiced) would otherwise raise a false alarm.
- **Chord stacks.** `chords-jiang` names chords from a fixed vocabulary and
  cannot express full stacks; adding note extensions from Basic Pitch gave a
  noisy second reading. The bass stem's lowest note names the bass better
  than Jiang (0.74 against 0.62 agreement with tabs).
- **Unpinned packages.** `stems` and `stems-6s` still list `torch` and `numpy`
  without versions (only the demucs version is checked when they run).

## Matching and import

- **Match limit.** On the studio test one recording missed its Work by 0.005
  of distance; a limit near 0.125 is suggested, to be checked against the
  calibration set before it changes.
- **Few references.** A Work with few occurrences in the archive was not
  matched; it needs more reference recordings.
- **Profile cache in real imports.** Use `--profile-cache` in real imports
  once a release carrying it is deployed (the deployed import does not have
  the flag).
- **Identical corpus pieces.** Two pairs of corpus occurrences in the
  studio-test scratch copy have identical pieces (the profile cache shares
  one entry per pair); find out why.
- **Label candidate talk by ear.** The span detector's talk margin (ADR 0091)
  was fitted on accepted song time alone; nothing labels real talk. Listen
  to the 30 candidate spans given to the operator with ADR 0091 (recording
  and seconds; kept outside this repository, which holds no archival data):
  talk the rule keeps, outside and inside songs, and nominated spans it
  demotes near and far from the margin. Mark each talk or not. Then refit
  `talk_margin_db` (and check `talk_reference_percentile`)
  leave-one-session-out on both kinds of label, and change the preset only
  if the labels move it and the window calibration still holds.
- **Stem separation cost.** CPU separation takes about 33 s per minute of
  audio; GPU separation through Spool and stand-in stems are step 0 of the
  implementation order ([below](#step-0-bulk-import-at-scale-adr-0112)).

## Lead sheets and practice

- Show a slash chord only when the bass holds for at least half a bar.
- A beat-level toggle for chords that change every beat, and local key regions
  for spelling.
- Written rhythm (staccato rather than rests), syllable-timed lyrics and the
  bass line as its own staff: the ADR 0085 and 0086 amendments planned before
  0.0.10 is cut. The 60% staccato rule over-fires on singing (a third of all
  notes marked); a measured replacement from the lead-sheet viewer experiment
  (notes from the pitch curve, a constant onset offset removed, rhythm chosen
  by timing error plus a simplicity cost, every syllable on a note, bleed and
  octave errors dropped, a suggested clef) should replace it before 0.0.10
  merges, and apply to the bass line too.
- Melody, the long tail: free-time passages where the beat grid is wrong,
  syllables shifted by a misaligned lyric line, heavily ornamented singing
  reduced to its landing notes (a choice, not checked against a person's
  transcription), and whether the simpler rhythm (written onsets a median
  35-90 ms from sung ones) keeps the feel.
- Full-band transcription, a research pass before any build: the current best
  open models per part (solo piano, separated single-line horns and bass,
  drums, comping in a band mix), their licences against AGPL, and engraving
  from notes to a readable score (rhythm, swing, voices, spelling) per
  instrument. Score horn transcriptions against a published hand-transcribed
  jazz-solo database, which gives real references where the vocal work had
  none. Expected: melody, bass and drum grooves soon; horn solos good enough to
  study with work; full comping voicings need people's corrections.

- An experiment floor for every analysis task, the way to improve the
  pipeline continuously, whether a change is a tuned parameter, a new
  post-processing step or a fine-tuned model (most likely the melodic note
  extractors; making new models is not the aim). Pieces:
  - fixed evaluation sets per task (melody and bass notes, written rhythm,
    chords, beats and downbeats, key, talk spans, matching), drawn from
    people's corrections as they accumulate plus open references (a jazz-solo
    database, an aligned saxophone omnibook, guitar and bass sets), each split
    by song or session so nothing leaks between tuning and testing;
  - a harness that runs any pipeline variant against them from feature-bank
    outputs, so a variant reruns only the steps it changes, and records each
    result with the variant's versions and parameters;
  - cost as a first-class axis next to accuracy: runtime per minute of audio,
    memory, model size, and whether it could run on a phone;
  - a gate: a variant replaces the current one only when it is better on the
    held-out set and no worse on cost beyond a stated bound;
  - the training data's licences recorded with any tuned weights, since
    non-commercial data keeps its weights to the operator's machines.
  The calibrations already done (the detector's held-out sessions, the window
  matcher's report, the melody metrics) are early instances to fold in.
  The first instance is the horn set, [`enrich/bench`](../enrich/bench/README.md):
  separated horn solos scored against a jazz-solo database and an aligned
  saxophone omnibook, data private on the x86-64 laptop and referenced by
  digest, `basic-pitch` and `horn-d0` baselines, and a `bench-horn` suite that
  fails when a mean note F1 drops by more than 0.02. Still to come: other
  tasks' sets, variants run from feature-bank outputs, a held-out split for
  tuning, and cost in the gate.

## Step 0: bulk import at scale (ADR 0112)

Decided 2026-10-19 ([ADR 0112](decisions/0112-bulk-import-at-scale.md) and
[ADR 0090's amendment](decisions/0090-extractor-runs-travel-through-spool.md#amendment-2026-10-19-a-stem-is-a-stem));
the four pieces are built and merged together on `bulk/integration`
(2026-10-19), each item saying what is left. Measured: the backfill costs about 0.98 s of laptop
compute per audio-second; parallel copies and the Mac's GPU project 7.0x,
with song regions 12.7x. Each record's "For the operator to confirm" list
gives the proposed thresholds; build to them, and change them only on the
operator's answer.

Measured on `bulk/integration` (two profiled recordings on the laptop,
scratch banks): `--eager` reaches 0.337 s per audio-second saturated on the
laptop alone (2.9x) and 0.277 with stems on the Mac (3.5x, bound by the
worker's transfers, one task at a time); `--songs` at the shadow's song
coverage would reach 0.200 (4.9x). Below the projected 7x and 12.7x
because stems cost more under load and f0-pesto costs 0.37 core-seconds per
audio-second over its two passes. Before `--songs` is used on real data:
region stems disagreed with whole stems over the same span (bass SNR
-0.15 dB on a 485 s region). Diagnosed and fixed on `fast/integration2`
(2026-10-20): Demucs lays its segments from the start of what it is
given, so a cut separated alone is other averages; a region run now
separates the source over a window on the whole run's grid and keeps the
region's samples (`stems.aligned`, `context_of`; `extract.py` passes the
source). Tested with a grid-shaped stand-in model (the region's stems are
the whole's to one rounding step; the cut alone is not); not yet confirmed
on real audio with Demucs (expect above 90 dB and bass notes F1 at least
0.98 over the short recording's 35-520 s), nor in the bench. Readers now
take region entries (`bank.RegionView`, `enrich propose` per region, the
relay's region stems by key). A region that is the whole recording
is made whole. Round 2 (ADR
0112's amendment): the backfill makes every recording usable first (the
fast tier, then a proposal) and stems and what measures them in the
background by priority, requests first; it reports time-to-usable and
time-to-done, reserves each copy the cores it uses, takes its costs from
the cost gate's baseline, and never waits for ever for an absent worker.
Measured on a copy of the whole shadow: usable after about 22 minutes
with `chords-jiang` in the background tier (with it in the fast tier its
copies need up to 11.4 GB each on a 93-minute recording and a long one runs
nearly alone). Round 3 (built and merged on `fast3/integration3`, ADR 0112's last two
amendments; not yet measured on the shadow): the background queue no longer
idles the laptop (a job waiting for cores or memory does not hold the jobs
behind it, backfilling by the recorded costs; routed stems overflow here
whenever they would end here sooner); each recording is proposed as soon as
its fast units settle (the usable proposal was 23% of time-to-usable);
`chords-jiang` 2 bounds Jiang's memory (2 GB plus 0.8 GB an hour, not yet
measured on whole long recordings), so several copies fit; region runs of
`notes-basic-pitch`, `f0-pesto` and `melody` lie on the whole run's grid
(bench clips: note F1 0.89-1.0). Left: measure time-to-usable, core use and
overflow share on a copy of the shadow; the queued long-recording Jiang run
(replace 0.8 GB/h and the 0.151 cost); the region check on the 554 s
recording over 35-520 s, then whether `--songs` replaces `--eager`; one
Door admission for a recording's proposer runs (about 2 s a run is the
floor of a proposal today).
The Mac worker now gets the content as WavPack (73% of the
float WAV, decoding to its very samples) and can run two tasks at once:
on the two recordings, 0.223 s per audio-second end to end against 0.305
the same day for float WAV one at a time, with byte-identical stems. The
link is now the bound: off the home network the Mac pulls about 4.3 MB/s
and pushes 3.3, and a task moves 0.28 MB per audio-second in and 0.18 (the
stems' FLAC) out, so about 0.12 at best there; on the same network as the
laptop it should reach separation's 0.10 (not measured). The worker's
Spool configuration (`max_concurrent` 2) and its copy of `enrich/` are
the operator's to update.

Four pieces can be built at once, each on its own branch. `enrich/bank.py`
is shared: **gpu** owns `Bank.entry`'s lookup and the manifest check,
**triage** adds new functions beside them (keys, regions, requests) and
leaves `Bank.entry` alone, and **scheduler** consumes both and merges last.
**melody** is independent. Every piece runs on copies or scratch banks,
never the shadow archive or its bank, and passes the cost gate once
scheduler has added it.

- **scheduler.** Built (`enrich backfill`, the pool, Spool fan-out, a dry
  run by default; [the runbook](shadow-runbook.md#filling-the-feature-bank)),
  and joined to the other three on `bulk/integration`: modes `songs`,
  `eager` and `on-demand` from `regions.plan`, with the expensive units of
  `songs` run over each recording's `regions_for` song regions as region
  entries (`structure/playing` from the bank as the last source); bank
  requests planned first in every mode, a request for a measure of a stem
  planning the stem over its region too; stand-ins counted as made (the
  planner reads through `Bank`), and a routed extractor's
  `remote_parameters` (`stems` on the Mac with `apply.device` `mps`)
  refused at the start unless a passing equivalence record is in the bank,
  with a worker entry that does not stand in made here; `f0-pesto --from
  stems/vocals` ordered before `melody` so its probe is read. Left:
  region jobs on Spool (a task carries no region until
  triage's task `/2`); declaring the `bench-cost` suite with wf; a first
  dry run on a copy of the shadow, and a measured time-to-usable there.
  As decided: `enrich/backfill.py` and `enrich/enrich.py backfill`
  (ADR 0112's flags). Plan from the archive (the Artifacts `enrich propose
  --all` finds) and the bank: compute the mode's keys, drop those present
  or standing in, order requests, song regions, the rest. Regions come from
  triage's `regions_for(export, artifact)`; until it lands, plan whole
  content only. A pool of copies sized to cores and memory, threads per
  extractor from the configuration, setting `OMP_NUM_THREADS` and its
  kin (extract the slot and thread decision into a named, tested function).
  Route configured extractors through `enrich/remote.py put` and collect as
  results arrive; dependents wait. `--follow` reads `requests/`; `--dry-run`
  prints the plan and projected days from recorded costs. Report
  audio-seconds per wall-second. Tests (`enrich/test_backfill.py`, synthetic
  audio from `enrich/support.py`): a rerun over an unchanged bank runs
  nothing (replace the runner with one that raises); a run killed midway
  resumes and files each entry once; a failed run is recorded and not
  retried; each mode plans the keys it should. Also the `bench-cost` suite
  in `enrich/bench`: CPU-seconds per audio-second per extractor, version and
  machine against a recorded baseline, failing above the tolerance; declare
  it with wf's suites as `bench-horn` is. Replace the hand-written backfill
  scripts in [the shadow runbook](shadow-runbook.md).
- **gpu.** Built 2026-10-19 but for `f0-pesto` on MPS (see its last
  sentence): the stems record over six 60 s clips of two rehearsals passes
  (note F1 pooled 0.997 vocals and bass; f0 0.09 cents; voicing 100%);
  the set of ten clips from other recordings is still the operator's to
  confirm. `interchangeable` in the manifest check
  (`bank.check_manifest`) and in `stems`' `extractor.json` (update its
  `SHIPPED` pin); `extract.py equivalence` writing and validating the
  equivalence record (`archive-feature-bank-equivalence/1`) in the same
  change; `Bank.entry` looks up stand-ins only under a passing record whose
  software matches, and `entry_from` finds a measure over any in-tolerance
  stem, the configured device's first; the runner and `remote.py put`
  count a stand-in as `hit`, `--exact` asks for the configured key alone.
  Tests: a stand-in is found with a passing record and not without one, not
  with a failing one, and not with a software mismatch (each swallowed
  branch its own test). Then the Mac worker path end to end: Spool 0.0.4 on
  both machines, the grant, `worker.json` and a real `stems` run on the
  Mac's GPU into a scratch bank, collected and filed; measure the record
  over the agreed test set. `f0-pesto` on MPS was measured (2026-10-20, pesto
  2.0.1, torchaudio 2.11 and omegaconf added to the Mac's stems environment,
  under 10 MB): 0.039 s per audio-second a pass on the GPU against 0.12 on
  the Mac's CPU and 0.19 core-seconds on the laptop; over the two profiled
  recordings' vocal stems the GPU's track differs from the laptop CPU's by
  0.002 cents at the 99th percentile with every row's voicing the same (the
  bass stems again gave no voiced row). Not built: its two passes would add
  0.078 s per audio-second to the Mac, which is the backfill's bound
  (0.10 for separation), to save the laptop 0.046 at eight cores, and the
  `device` parameter rekeys every `f0-pesto` entry (ADR 0090 amendment, item
  4). Worth it only if the laptop becomes the bound.
- **triage.** Region keys (`archive-feature-bank-key/2`) and region entries
  in `enrich/bank.py`, with their validator in the same change; `extract.py
  run --region` cutting a pinned FLAC and recording the region and the
  cut's digest; `Bank.covering(content, extractor, start, end)` answering a
  whole or containing region entry; `remote.py` task
  `archive-extractor-task/2` carrying the cut, and collect cutting again to
  check it. `enrich/regions.py`: `regions_for` from accepted song
  occurrences, else the detector's song suggestions, widened, snapped and
  merged. Bank requests (`archive-feature-bank-request/1`, `requests/` named
  by key) written by `relay/practice.py` (a `queued` status in
  `relay/openapi.json` when the bank has a scheduler, the
  `stems_not_made` refusal otherwise), by `enrich propose`, and by
  `enrich/live.py compare` once it exists; proposers read region entries
  with the region's offset. Tests: a whole-content key is unchanged by the
  change (pin one); a region entry's outputs land at the right recording
  times; a span edited within its margin is still covered; a second request
  is the same file; the relay answers `queued`, never an empty result.
  Measure on shadow copies how much of the audio song regions cover, with
  and without detector spans.
  *Built (branch `bulk/triage`):* region keys and entries with their
  validator, `extract.py run --region`, `bank.covering`, bank requests
  (`bank.ask`, `bank.requests`), `enrich/regions.py` (span precedence,
  `regions_for`, `plan` per mode from recorded costs), and the relay's
  `bank_scheduler` with `queued` and `feature_queued`. *Left:* `remote.py`
  task `/2`; requests from `enrich propose` and region offsets in the
  proposers; the coverage measurement on shadow copies.
- **melody.** Decide before `melody`'s mix pass, cheaply, whether the vocal
  stem will fall short, so the extractor runs once rather than twice (the
  profile: the vocal pass falls short and the 151 s mix pass follows it on
  a 2715 s rehearsal). Measure on shadow copies how often the vocal pass
  falls short and what the mix melody gives there; candidates are the
  vocal stem's `f0-pesto` voicing (already made) or its level, read through
  `prefers`' `at_least` so the runner and every reader
  (`Bank.default_input`) still agree on which entry is the default. Keep
  the melody metrics and the bench's F1 within their gates, and show the
  saving in the cost gate; bump `melody`'s version only if its outputs can
  change. Tests in `enrich/test_melody.py` and `enrich/test_bank.py`
  (preferred inputs).
  Built (branch `bulk/melody`): a preference's `probe` (ADR 0076's second
  amendment), `f0-pesto`'s confidence on the vocal stem; `melody` stays
  version 3. The scheduler runs `f0-pesto --from stems/vocals` before
  `melody` (`backfill.unit_order`). Left for the cost gate: the saving, once `bench-cost`
  exists.

## People, samples, and rehearsals (ADRs 0092-0111)

The operator accepted ADRs 0092 to 0111 on 2026-10-19 as decisions only:
none of their contracts, routes, or commands is built, and each installs
with its implementation in a later specification release.

- **Implementation order** (operator, 2026-10-19): (0) bulk import at
  scale ([ADR 0112](decisions/0112-bulk-import-at-scale.md), with
  [ADR 0090's amendment](decisions/0090-extractor-runs-travel-through-spool.md#amendment-2026-10-19-a-stem-is-a-stem)),
  first because every later step adds extractors or readers of the bank
  ([its build work](#step-0-bulk-import-at-scale-adr-0112)); (1) lineage, origin, and
  rights by use ([ADR 0098](decisions/0098-content-lineage.md),
  [ADR 0096](decisions/0096-origin-of-recordings.md),
  [ADR 0097](decisions/0097-rights-by-use.md)); (2) selection version 3,
  sample renders, packs, and the index, giving the operator a sample app on
  the tailnet ([ADR 0099](decisions/0099-samples.md),
  [ADR 0100](decisions/0100-packs-and-collections.md),
  [ADR 0102](decisions/0102-retrieval-index.md)), with DAW projects
  ([ADR 0101](decisions/0101-daw-projects.md)) alongside; (3) audiences, grants,
  participation, and Cloudflare, so friends get in
  ([ADR 0093](decisions/0093-audiences.md),
  [ADR 0094](decisions/0094-peoples-writes.md),
  [ADR 0095](decisions/0095-participation.md),
  [ADR 0092](decisions/0092-internet-exposure.md)); (4) voice capture, ASR,
  and the notebook ([ADR 0103](decisions/0103-voice-capture.md),
  [ADR 0104](decisions/0104-transcription-and-speakers.md),
  [ADR 0105](decisions/0105-notebook.md)); (5) live
  ([ADR 0106](decisions/0106-set-lists-and-clocks.md),
  [ADR 0107](decisions/0107-multi-device-and-live.md),
  [ADR 0108](decisions/0108-live-analysis.md),
  [ADR 0109](decisions/0109-rehearsal-node.md),
  [ADR 0110](decisions/0110-shared-clock.md),
  [ADR 0111](decisions/0111-pad-maps.md)). Pad maps come last within
  step 5: they build on lineage (step 1), sampler banks (step 2) and MIDI
  capture (step 5).
- **Notification and watch spike.** A self-hosted push service on the
  tailnet with action buttons, and web push from a home-screen web app,
  first; whether actions and voice replies work on the watch is unverified.
  The destination is a native phone and watch companion app, which is also
  the phone and tablet microphone node (background recording).
- **Link licence check before the node is first published.** The rehearsal
  node is tempo master and syncs software and devices by Ableton Link
  (hardware by MIDI clock). Check Link's licence against AGPL before the node
  application is first published, and so before any of it could enter this
  repository; record the result as a short decision record. The node
  repository is AGPL-3.0-or-later if it passes; Link stays in the node
  application either way ([ADR 0110](decisions/0110-shared-clock.md)).
- **Sample identification spike.** Locally, on a public or test corpus
  first, with no third-party uploads; friends' tracks with known samples once
  their custody is in place. Its output is machine-proposed lineage claims
  ([ADR 0098](decisions/0098-content-lineage.md)).
- **Embedding-model spike.** Choose the timbre extractor's model for the
  index's embeddings ([ADR 0102](decisions/0102-retrieval-index.md)) from two
  or three candidates whose weights' licence is verified to permit
  redistribution, judged by the operator listening to the neighbours of
  twenty library sounds.

## Operations

- Deploy Spool 0.0.4 to both machines, add the GPU-stems grant, and give
  `wf check` (agent-workshop) a worker on the x86-64 laptop.
- Decide when to delete: the studio-test scratch copy (29 GB), the shadow's
  read-only snapshot from before 0.0.9, and the original scratch copies of the
  experimental album claims (a verified copy exists).
