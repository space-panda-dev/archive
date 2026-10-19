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
- **Melody sources.** `melody` prefers only `stems/vocals`; `stems-6s/vocals`
  could be added.
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
- **Detector presets.** The detector preset copied from live jams labels
  studio vocals as talk. A studio preset is needed; an audit of accepted
  occurrences is under way.
- **Stem separation cost.** CPU separation takes about 33 s per minute of
  audio; GPU separation through Spool (ADR 0090) waits for Spool 0.0.4 on both
  machines and a grant.

## Lead sheets and practice

- Show a slash chord only when the bass holds for at least half a bar.
- A beat-level toggle for chords that change every beat, and local key regions
  for spelling.
- Written rhythm (staccato rather than rests), syllable-timed lyrics and the
  bass line as its own staff: the ADR 0085 and 0086 amendments planned before
  0.0.10 is cut.

## Operations

- Deploy Spool 0.0.4 to both machines, add the GPU-stems grant, and give
  `wf check` (agent-workshop) a worker on the x86-64 laptop.
- Decide when to delete: the studio-test scratch copy (29 GB), the shadow's
  read-only snapshot from before 0.0.9, and the original scratch copies of the
  experimental album claims (a verified copy exists).
