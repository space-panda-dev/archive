# 0071. Cover similarity is measured with Essentia, as the V2 system calibrated it

Status: accepted; amended 2026-10-18 (window matches are decided by the
quarter-rate distance, with near ties confirmed exactly)

The work matcher in `enrich/` (ADR 0067) implements the cover-song measure of
Serrà, Serra, and Andrzejak (2009) in numpy and scipy, under its own method
identifier. It was written that way on the belief that Essentia, which the
V2 system used, could not be obtained reproducibly for both the flake and a
macOS virtual environment. That belief was wrong. The V2 system installed
Essentia from wheels pinned by SHA-256, built under Nix, for CPython 3.14 on
x86_64 Linux and arm64 macOS.

What the V2 system learned is tied to that build. Its thresholds were
measured with Essentia's distances and its growing-window search:

- a suggestion distance of 0.08, with one false positive in 2,779 controlled
  pairs of the operator's own recordings;
- about 0.11 for commercial releases, from eight positives;
- a prefilter keeping half of each query's corpus, which kept every one of
  29 labelled positives;
- a stop after two growth steps without improvement, or after one step
  worse than the best by more than 0.15.

The numpy method's scores are on another scale, so none of that applies to
it. It could only be trusted after the same calibration was done again.

## Decision

**The matcher measures cover similarity with Essentia.** The method is
`archive-enrich/essentia-serra09` version 1, with this pipeline:

1. ffmpeg decodes the compared audio to mono 16-bit PCM at 22,050 Hz. The
   samples are scaled to floating point by dividing by 32,768, which is
   what the V2 system's loader did with the same file.
2. Frames are 4,096 samples with a hop of 2,048, as Essentia's frame
   generator cuts them.
3. Each frame passes through:
   - a Blackman-Harris 62 dB window;
   - the magnitude spectrum;
   - spectral peaks, ordered by magnitude, with a threshold of 1e-5,
     40 to 5,000 Hz, and at most 10,000 peaks;
   - a pitch-class profile (HPCP) with Essentia's defaults.
4. `ChromaCrossSimilarity` compares the two profile sequences with the
   optimal transposition index, non-binary, a binarisation percentile of
   0.095, and a frame stack of 9.
5. `CoverSongSimilarity` aligns them with `serra09` and the `asymmetric`
   distance.
6. Steps 4 and 5 run in both directions, and the distance is the smaller of
   the two.
7. The score is that distance rounded half to even to four places. Its scale
   is `archive-enrich/essentia-serra09/smaller-asymmetric-distance`, and lower
   is closer.

**No alignment is an answer.** The serra09 distance divides by the best
alignment score. When the cross-similarity has no match in either
direction, Essentia answers positive infinity. One second of music against
silence does this. Such an entry is counted as compared and unaligned. It is
never ranked or proposed, and it has no score. Any other answer that is not
a finite, non-negative number is still a failure of the run.

**The build is pinned exactly.** It is Essentia 2.1b6.dev1438 under CPython
3.14. That release ships wheels for no other interpreter. The flake builds
the matcher's interpreter from wheels pinned by SHA-256 for x86_64 Linux and
macOS. A virtual environment installs the same wheels with
`--require-hashes`. Where no pinned wheel exists, the matcher refuses to
run. Another Essentia build is another method version, and its thresholds
must be measured again.

**The V2 system's thresholds carry over.** A match is suggested when its
score is at or below 0.08. The archive does not yet record whether corpus
audio is the operator's own recording or a commercial release. So the
release threshold of 0.11 is not used, and every corpus entry is judged at
0.08. That is the conservative side: it is the only threshold with a
measured false-positive rate. The search settings and the prefilter
retention are the V2 system's, recorded in each run's parameters.

**The numpy method stays.** `archive-enrich/chroma-oti-qmax` remains
available under its own identifier. It is uncalibrated, and the capture
import does not run it.

**The licence fits.** Essentia is AGPL-3.0, and this repository is
AGPL-3.0-or-later.

## Consequences

- Before suggestions on the real archive are trusted, the method is checked
  against the shadow's accepted performances. This is a check of carried-over
  thresholds, not a new calibration.
- Each comparison costs about two seconds on one core, as the V2 system
  measured. The prefilter halves the alignments, and comparisons run in
  parallel workers.
- The matcher's interpreter is Python 3.14 while World's stays 3.13. The flake
  wraps `enrich` and `capture` with the matcher's interpreter.

## Alternatives considered

- **Keep the numpy method and calibrate it.** It repeats months of measurement
  to reach thresholds already known for Essentia.
- **Build Essentia from source under Nix.** That is a different build from
  the one calibrated, it is not in nixpkgs, and it is a large build to
  maintain.
- **Move every Python part to 3.14.** nixpkgs does not cache its 3.14 package
  set, so CI would compile scipy and the RDF stack from source.

## Amendment, 2026-10-18: window matches are decided by the quarter-rate distance

Status: accepted. The operator decided this on 2026-10-18.

Deciding every window match by Essentia's exact distance costs too much.
Today's growing-window search takes about 55 worker-hours on Essentia for the
19.8 recording-hours of the calibration workset. On the bit-exact NumPy port it
still takes about 7 CPU-hours. The operator asked for a matcher about a hundred
times faster than Essentia, not eight.

The whole-recording search in the amendment to
[ADR 0072](0072-windowed-work-matches.md) does it in about half a CPU-hour. It
decides with a cheaper distance and keeps the exact distance for the near ties
the cheap one cannot settle.

**The method of `enrich match-windows` is
`archive-enrich/whole-recording-serra09` version 1.** It gets a new identifier
because most of its scores are on a new scale. Steps 1 to 3 above are
unchanged: the decoding, the frames, and the HPCP profiles. So is the pinned
Essentia build, which still measures every profile. Claims already made under
`archive-enrich/essentia-serra09` keep their method and scale, and nothing
reinterprets them.

**The quarter-rate distance** compares two profile sequences, a window's and a
corpus occurrence's:

1. Average each sequence over consecutive groups of 4 frames, then divide each
   averaged frame by its largest bin (a frame of zeros stays zero). Drop a
   trailing group of fewer than 4 frames. A sequence of 10 or fewer
   quarter-rate frames is too short to compare. It is counted as such, never
   as unaligned.
2. Pick the key shift: of the 12 rotations of the occurrence's mean frame, the
   first with the largest dot product with the window's mean frame. Rotate the
   occurrence's bins by it.
3. Stack every 9 consecutive frames into one vector. A sequence of n frames
   gives n − 8 stacks.
4. Compute the squared Euclidean distance of every pair of stacks in float32,
   as |a|² + |b|² − 2 a·b, with the cross term taken from one matrix product.
5. Set a cell to 1 when its distance is at most both its row's and its
   column's order statistic. For a row or column of length m, that is the value
   at zero-based index int(0.095 × m) − 1, never below 0, among its values
   sorted from the smallest, with no interpolation.
6. Score the matrix with serra09, the port of Essentia's recurrence with its
   penalties. The other direction's matrix is the transpose of this one, so
   the distance is the square root of the matrix's smaller dimension divided by
   the best score, rounded to float32. That is the smaller of the two
   directions' distances. When nothing matches, the distance is positive
   infinity, and **No alignment is an answer** applies as above.
7. Round the score half to even to four places, as above. Its scale is
   `archive-enrich/whole-recording-serra09/quarter-rate-distance`, and lower is
   closer.

**A window is suggested** when its closest Work's quarter-rate score is at or
below 0.118. Its closest Work is the Work of its closest corpus occurrence,
compared against every occurrence in the corpus.

**Near ties are confirmed exactly.** Take a suggested window whose closest Work
beats the next Work by less than 0.01475 in raw quarter-rate distance.

- That window is decided again by the exact distance: Essentia's distance, by
  the bit-exact port, measured on the window's audio decoded afresh, not on a
  slice of the whole recording's profiles.
- It is compared with the occurrences of its candidate Works, and with the
  half of the corpus the mean-chroma prefilter keeps.
- It is suggested only when its exact score is at or below 0.08, for the
  exact distance's closest Work.
- Its claim then carries the exact score and the exact scale,
  `archive-enrich/essentia-serra09/smaller-asymmetric-distance`. The scale is
  how a reader tells the two kinds of claims apart.

The margin was fitted as 0.01 on a scale where 0.118 maps to 0.08. That is
0.01475 in quarter-rate units. Every other window is decided by the
quarter-rate distance alone, and the exact distance never runs for it.

**Why exact equivalence is no longer the decision.** The exact distance was
worth its cost because it carried the V2 system's measured false-positive rate.
That rate was measured with the V2 system's growing-window search, which this
amendment's companion retires.

On the archive's own calibration workset, the new design compares as follows.
The search with confirmed near ties made fewer wrong suggestions than the exact
distance in today's search: 3 against 4 under the lenient rule, and 0 against 2
under the strict rule. It also suggested 20 more accepted occurrences: 65
against 45, and 56 against 38 under the strict rule. The quarter-rate distance
alone made 6 wrong suggestions. The confirmation of near ties is what brings
that down to 3. The measurements and their caveats are in
[ADR 0072](0072-windowed-work-matches.md)'s amendment.

**What stays exact:**

- the profiles, from the pinned Essentia build;
- the serra09 recurrence, the port used by every comparison;
- the decision of every near tie: the full exact distance, on freshly decoded
  audio;
- the exact cross-similarity port, which is the near-tie path. It is also what
  `cover.py <query> <reference>...` prints, under the
  `archive-enrich/essentia-serra09` method as before.

**Quarter-rate distances are not bit-identical across machines.** Their float32
matrix products depend on the BLAS library numpy uses. A run records that
library in its software. A run with another library is another run. The same
library, inputs, and parameters give the same answer, whatever the number of
processes.

**The V2 thresholds describe only the exact scale.** The false-positive rate of
one in 2,779 pairs, and the release threshold of about 0.11, apply to exact
scores. Neither 0.118 nor 0.01475 has a measured false-positive rate outside
the calibration workset that chose them. That is an
[open question](../open-questions.md).

### Consequences of the amendment

- A window match's score is on one of two scales, named in the claim. Scores on
  different scales are never compared with each other.
- Essentia remains a dependency. It measures the profiles, and the exact path
  is checked against it bit for bit.
- The thresholds are a recalibration, not a carry-over. They were fitted on 98
  accepted occurrences, so the calibration harness checks them on any archive
  before its suggestions are trusted.

### Alternatives considered in the amendment

- **Keep the exact distance for every decision, on the port.** It takes about
  7 CPU-hours, 14 times more. It suggested 45 accepted occurrences rather than
  65, because reach comes from the search, not from the distance.
- **Use the quarter-rate distance alone.** It is a little cheaper, but makes 6
  wrong suggestions against 3, all at near ties.
- **Rescale quarter-rate scores so that 0.118 reads as 0.08.** The prototype
  did this so it could reuse the old judge. A claim would then show a number on
  Essentia's scale that Essentia never measured.
- **Confirm near ties on slices of the whole-recording profiles.** It is free,
  but a slice differs from a fresh measurement by 0.0034 at the median, and by
  up to 0.108. That error is as large as the margins being decided. A fresh
  decode costs about 2 CPU-seconds per window, and about 10 windows in 20
  recording-hours need one.
