# 0071. Cover similarity is measured with Essentia, as the V2 system calibrated it

Status: accepted

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
