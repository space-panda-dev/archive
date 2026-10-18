# 0072. A work match can suggest a window of a recording

Status: accepted; amended 2026-10-18 (a whole-recording search replaces the
growing windows)

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
- **No alignment.** Entries that do not align (ADR 0071) are counted in the
  step but not ranked. A step where nothing aligns has no closest entry.
  Before any best step, the window keeps growing. After one, the step is
  worse than the best by more than any margin, so growth stops.
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

## Amendment, 2026-10-18: a whole-recording search replaces the growing windows

Status: accepted. The operator decided this on 2026-10-18.

The record stays as it is. `archive.claim/work_match` version 3,
specification 0.0.8, and Door's `x-archive-span-window` rule are unchanged, and
so is the run that pins the search's parameters. What changes is the search
that the decision above recorded as the matcher's method.

The single start point limits reach. In the V2 system it reached 57.5% of
accepted performances. On the archive's calibration workset (17 recordings,
19.8 recording-hours, 98 accepted occurrences of 44 Works), 27 accepted
occurrences lay in no window that today's search evaluates. A prototype that
compared every corpus occurrence along the whole recording, with exact
distances, touched all 98. It took 59.6 CPU-hours. The quarter-rate distance
of [ADR 0071](0071-essentia-cover-similarity.md)'s amendment makes the same
search cheap.

**The search.** The method is `archive-enrich/whole-recording-serra09`
version 1. Every value below is recorded in the run's parameters.

1. **Measure once.** The recording's stream, from sample 0 to the pinned stream
   claim's `sample_count`, is decoded and measured once by ADR 0071's pipeline.
   It is decoded in blocks, so memory does not grow with the recording's
   length. The profiles take about 1.9 MB per recording-hour. A window's
   profiles are a slice of them:
   - a source sample S at rate r is analysis frame round(S × 22,050 / r /
     2,048);
   - a window of L samples spans ceil(round(L × 22,050 / r) / 2,048) + 1
     frames.

   Corpus occurrences are measured as before.
2. **Candidate pass.** For each corpus occurrence, the recording's quarter-rate
   profiles are cut into chunks:
   - each chunk is 2 occurrence-lengths long, and a new one starts every
     occurrence-length;
   - the last chunk ends at the recording's end.

   Each chunk is compared with the occurrence much as the quarter-rate distance
   compares, with three differences:
   - the key shift is Essentia's transposition index of the chunk against the
     occurrence;
   - n frames give n − 9 stacks;
   - the order statistic's index is int(0.095 × m), with no − 1.

   Up to 2 disjoint alignments are traced per chunk, the best first. An
   alignment's distance is the square root of the occurrence's stack count
   divided by its score. An alignment within 0.20 is a hit. A hit names the
   recording frames its alignment spans.
3. **Regions.** Two hits of one occurrence that overlap by at least half the
   shorter merge into the closer one. Then hits from all occurrences are taken
   closest first, and each joins the first region it overlaps by at least half
   the longer of the two, or starts a new one. A region's candidate Works are
   the Works of its hits, closest first, at most 5.
4. **Snapping.** A region becomes windows of whole suggested spans:
   - the spans considered are the `music` and `unknown` spans that the region
     overlaps and that are not yet claimed;
   - they form contiguous groups. A claimed span splits a group. A `talk` or
     `quiet` span between two considered spans does not;
   - of the groups, the one the region overlaps most is used;
   - it gives two variants. The outer one is the group's extent. The inner one
     runs from the first to the last span of which the region covers at least
     half, or is the outer one when there is none;
   - a variant shorter than two seconds is dropped.

   Every edge is a span edge, so `x-archive-span-window` holds.
5. **Claim rounds.** Each window is compared, by the quarter-rate distance,
   with the occurrences of its candidate Works. Those are its region's Works
   and up to 8 Works whose hits overlap the window by at least half the
   shorter. Then rounds repeat:
   - the window with the smallest distance is kept, and its spans are claimed;
   - every other region is snapped again without the claimed spans;
   - a region left with nothing is absorbed.

   Kept windows never share a span.
6. **Decision.** Each kept window is compared, by the quarter-rate distance,
   with every occurrence in the corpus. There is no prefilter. The window is
   suggested at or below 0.118, and its near ties are confirmed by the exact
   distance at 0.08, as ADR 0071's amendment states.
7. **Claims.** A suggested kept window yields one claim, for its closest Work.
   The claim carries:
   - the window;
   - as its corpus, that Work's occurrences that aligned on the path that
     decided it;
   - the closest occurrence's score, with that path's scale.

   All claims come from one derivation run in one Door batch.

The corpus is the one defined above. Occurrences from the compared recording or
its session never enter any of these steps.

**What it replaces, and why:**

- **Windows no longer grow from the first span of each run.** The candidate
  pass finds starts anywhere in the recording. This answers the open question
  of whether the search should start from more than the first span.
- **A window may cross a `talk` or `quiet` span** that lies between two
  `music` or `unknown` spans. The decision above bridged no talk. A hit that
  spans the gap is evidence that the song goes on through it, as in a quiet
  passage that was classed `quiet`. The cost is more windows that span two
  songs.
- **There is no growth and no stop rule.** The eight-span limit, the two stall
  steps, and the 0.15 degrade margin are retired.
- **The decision compares the whole corpus.** The prefilter now only widens
  the set an exact near-tie confirmation compares.
- **There is one claim per window, for its closest Work.** It is no longer one
  claim for every Work within the distance. Different Works' quarter-rate
  scores sit close together. At the 101 windows that the quarter-rate distance
  alone suggested, 58 further Works fell within 0.118, and none of those
  suggestions was judged. Near ties go to the exact distance instead.

**What was measured** on the calibration workset. In each cell, the lenient
count comes first and the strict one second.

- Under the strict rule, a window is judged only against accepted occurrences
  that cover more than half of it.
- An occurrence counts as suggested only through a correct suggested window
  that it covers more than half of.
- Occurrences are counted out of 98.

| Search | Suggested | Correct | Wrong | Unaccepted | Occurrences suggested | Cost |
|---|---|---|---|---|---|---|
| Growing windows, exact distance (as first decided) | 60 | 48 / 41 | 4 / 2 | 8 / 17 | 45 / 38 | about 55 worker-hours on Essentia; about 7 CPU-hours on the port |
| Whole recording, quarter-rate distance alone | 101 | 70 / 60 | 6 / 3 | 25 / 38 | 64 / 55 | 0.40 CPU-hours; 6.4 minutes on 4 processes |
| Whole recording, near ties confirmed (this amendment) | 97 | 71 / 61 | 3 / 0 | 23 / 36 | 65 / 56 | about 0.5 CPU-hours |

For the second row, the cost splits as follows:

- the candidate pass took 728 CPU-seconds;
- claim rounds took 433;
- decisions took 276;
- measuring the 17 recordings whole took about 0.06 CPU-hours more;
- measuring the corpus is the same in every row and is left out.

**The caveats:**

- **The numbers are in-sample.** The hit limit of 0.20, the distance of 0.118,
  and the margin of 0.01475 were all fitted on these 98 occurrences.
  Leave-one-recording-out chose 0.118 in every fold on the earlier prototype's
  windows. Its predicted wrong count did not carry over, though: it predicted
  1, and this search's windows gave 6 with the quarter-rate distance alone.
- **The third row is a replay.** Its 10 near-tie windows took their exact
  decisions from a run that decided the same windows exactly, on slices of the
  whole-recording profiles. Confirmation on freshly decoded audio, as decided
  here, has not yet run on the workset. A slice differs from a fresh
  measurement by 0.0034 at the median.
- **There is a ceiling.** Only 77 of the 98 occurrences have a corpus
  occurrence outside their own session. No search can suggest the other 21.
- **More suggestions are unaccepted:** 23, against 8 today. On the earlier
  prototype, about 6 of its 24 looked like real plays that nobody accepted, and
  7 lay where nothing is accepted. A listening pass would settle precision.
- **Boundaries are no better.** On the earlier prototype, which snaps the same
  way, the median edge error of correct suggestions was about 50 seconds,
  against 20 to 31 seconds today. Its median suggested window was 384 seconds,
  against 320 today and 290 for accepted extents. It had 11 windows spanning
  two accepted songs, against today's 4.

### Consequences of the amendment

- Cost grows with the corpus times the recording's length. Every occurrence
  passes along the whole recording, and every kept window is compared with
  every occurrence. On the workset that came to about 73 CPU-seconds per
  recording-hour, at about 92 occurrences.
- Accepting a new occurrence changes a run's inputs, so the next run searches
  the whole recording again. There is no incremental path.
- The calibration harness measures each recording whole and splits its work
  per recording, not per run.
- Hits, regions, alignment paths, and per-occurrence presence along the
  recording are computed but not recorded. Only the command's answer reports
  them. Whether any of them becomes a recorded output is open.

### Alternatives considered in the amendment

- **Keep growing windows, on the exact port.** It takes about 7 CPU-hours, and
  reach stays at 45 suggested occurrences.
- **Search the whole recording with exact distances throughout.** The
  prototype suggested 62 occurrences and made 1 wrong suggestion. It took
  59.6 CPU-hours as run, and would take about 29 in a single pass.
- **Move window edges onto the analysis frame grid**, so that slices are
  exact. A window would then no longer start on a span edge, which this record
  requires.
- **Record the change as a new decision.** The record, the Door rule, and the
  search's place in the run all stand. Only the search's mechanism changes, so
  the operator chose to amend the decision, as earlier changes to this record
  were made.
