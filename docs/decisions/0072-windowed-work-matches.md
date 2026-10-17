# 0072. A work match can suggest a window of a recording

Status: accepted

A fresh recording reaches World as an Artifact with suggested spans
(ADR 0066). A version 2 `work_match` (ADR 0067) compares a song occurrence or
an audio selection, and a fresh recording has neither until a person makes
one. So nothing can yet suggest which song a stretch of new audio is, or
where it starts and ends.

The V2 system answered both with one search:

1. split a recording's candidate spans into runs at talk;
2. grow a window from the start of each run, one span at a time;
3. at each step, rank the window against the corpus of accepted performances;
4. stop after two steps without improvement, or after one step worse than
   the best by more than 0.15;
5. roll back to the best step.

The best window is both the suggested Work and the suggested extent. On
2026-10-16 the operator decided that a match suggests the extent as well as
the Work, so a review client can offer the window as a ready-made selection.

## Decision

**`archive.claim/work_match` version 3 compares a window of one span
suggestions claim.** It pins:

- exactly one `suggestions` reference, an `archive.claim/span_suggestions`
  version 1 Claim revision;
- exactly one `work`, a `musical_work` Entity revision;
- one or more `corpus` references, each an `archive.claim/song_occurrence`
  version 2 Claim revision, naming distinct objects;
- exactly one `run`, an `archive.claim/derivation_run` version 1 Claim
  revision;
- at most one `parameters` Artifact.

Its payload has the version 2 fields `method`, `score`, `scale`, and
`direction`, plus:

- `sample_rate`, which MUST equal the pinned suggestions' `sample_rate`;
- `window`, an object of `start_sample` and `end_sample`. `start_sample`
  MUST be the `start_sample` of one of the pinned suggestions' spans.
  `end_sample` MUST be the `end_sample` of that span or of a later one.

The window is therefore a run of whole suggested spans. It lies within the
stream the suggestions are measured against, and it cannot be empty. Door
checks each rule with a new profile rule, `x-archive-span-window`.

**A window is a suggestion.** It accepts nothing, as a suggested span
accepts nothing. A person accepts audio by proposing an `audio_selection`,
which may take the window's edges or any others. A match never establishes a
Work, at any version (ADR 0040).

**Version 2 stays the form for a whole occurrence or selection.** Both
versions are installed. Neither supersedes the other, because they compare
different things.

**Specification 0.0.8 installs version 3** beside every 0.0.7 bundle, with
the 0.0.7 layout and core context. The release is additive, so a 0.0.7
archive adopts it by the specification upgrade (ADR 0068).

**The search is the matcher's method, recorded in its run.** The method is
Essentia's measure (ADR 0071). Its settings are the V2 system's and are
recorded in the parameters:

- **Runs.** A run is a maximal sequence of consecutive `music` or `unknown`
  spans. A `talk` or `quiet` span ends it, and no talk is bridged.
- **Windows.** A window starts at a run's first span and grows to at most
  eight spans. A window shorter than two seconds is skipped without counting
  as a step.
- **Prefilter.** For each window, a transposition-invariant mean-chroma
  prefilter keeps half of the corpus for alignment.
- **Corpus.** The corpus is every head song occurrence that a head
  `performance_work` version 3 link pins to a Work. Occurrences whose audio
  comes from the compared recording, or from another part of the same
  session, are left out.

A run yields claims only when its best window comes within the suggestion
distance of a Work's occurrence. It yields one claim per such Work. The claim
carries:

- the best window;
- that Work's compared occurrences as its corpus;
- the Work's best distance as its score.

**The capture import runs the match** after span detection, for the primary
part of each session.

## Consequences

- World projects version 3 matches with their window. A client can draw the
  window over the suggestions it came from and offer its edges.
- Reach is limited by the single start point. In the V2 system, only 57.5% of
  accepted performances lay within a window this search evaluates. More start
  points would be a new method version, measured before it is adopted, and
  this is an open question.
- Matching a new recording costs one alignment per window step and retained
  corpus entry. That is the slowest step of an import.

## Alternatives considered

- **Window bounds as span indices.** That is compact, but every reader would
  have to resolve the suggestions to find the audio. Sample bounds that Door
  ties to span edges are both self-describing and consistent.
- **Draft selections for the windows.** ADR 0047 keeps review states out of
  claims, and a selection is an acceptance.
- **One claim per window naming every close Work.** Version 2 already names
  one Work per claim, and a reader filters by Work.
- **Extend version 2 with a third compared form.** The profile has no
  conditional rules, so a window could not be required exactly when
  suggestions are compared.
