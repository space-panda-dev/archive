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
nothing below is built. Measured: the backfill costs about 0.98 s of laptop
compute per audio-second; parallel copies and the Mac's GPU project 7.0x,
with song regions 12.7x. Each record's "For the operator to confirm" list
gives the proposed thresholds; build to them, and change them only on the
operator's answer.

Four pieces can be built at once, each on its own branch. `enrich/bank.py`
is shared: **gpu** owns `Bank.entry`'s lookup and the manifest check,
**triage** adds new functions beside them (keys, regions, requests) and
leaves `Bank.entry` alone, and **scheduler** consumes both and merges last.
**melody** is independent. Every piece runs on copies or scratch banks,
never the shadow archive or its bank, and passes the cost gate once
scheduler has added it.

- **scheduler.** `enrich/backfill.py` and `enrich/enrich.py backfill`
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
  over the agreed test set. Last, if a small environment with pesto and
  torch fits the Mac's disk: `f0-pesto` with a `device` parameter (ADR
  0090 amendment, item 4), its equivalence record, and its speed on MPS.
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
  version 3. Left for the scheduler: run `f0-pesto --from stems/vocals`
  before `melody`, or the probe is not in the bank and the vocal stem is
  measured as before. Left for the cost gate: the saving, once `bench-cost`
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
