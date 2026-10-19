# Enrichment

Enrichment derives new evidence from existing evidence without changing its
inputs ([ADR 0006](../docs/decisions/0006-enrichment.md)). Its code lives here
([ADR 0064](../docs/decisions/0064-derivation-runs.md)): each tool takes files
and returns a plain result, and one small adapter turns a result into an
admission proposal for `door admit`, with the derivation run that made it.
Nothing here reads an archive except through `archive export`, and nothing
here writes a record: Door decides every revision.

| File | What it is |
|---|---|
| [`adapter.py`](adapter.py) | The adapter: reading the archive, staging parameters, and proposing a run with its outputs |
| [`probe.py`](probe.py) | The audio stream probe, a standalone tool ([ADR 0065](../docs/decisions/0065-audio-stream-facts.md)) |
| [`detector.py`](detector.py) | The span detector, a standalone tool ([ADR 0066](../docs/decisions/0066-span-suggestions.md)) |
| [`match.py`](match.py) | The numpy work matcher, a standalone tool ([ADR 0067](../docs/decisions/0067-matches-and-alignments-pin-runs.md)); uncalibrated |
| [`cover.py`](cover.py) | The measure: Essentia's profiles, measured per span or for a whole recording, the quarter-rate distance, and the bit-exact port of Essentia's distance ([ADR 0071](../docs/decisions/0071-essentia-cover-similarity.md)) |
| [`windows.py`](windows.py) | The whole-recording window search: candidate pass, regions, snapping, claim rounds, and decisions, a standalone tool ([ADR 0072](../docs/decisions/0072-windowed-work-matches.md)) |
| [`calibrate_windows.py`](calibrate_windows.py) | Checks the window matcher's thresholds against an archive's accepted occurrences; proposes nothing |
| [`enrich.py`](enrich.py) | The `enrich` command that runs a tool against an archive's records |
| [`bank.py`](bank.py), [`extract.py`](extract.py), [`extractors/`](extractors/frames/extractor.json) | The feature bank's format and reader, the runner that fills it, and the extractors (one table under [`extract.py`: the feature bank](#extractpy-the-feature-bank) says what each writes): `frames`, `loudness-r128`, `rhythm`, `tonal`, `melody`, `structure`, `quality`, `colour`, `stems`, `beats-beatthis`, `chords-jiang`, and `chords-chordino` ([ADR 0073](../docs/decisions/0073-feature-bank.md), [ADR 0074](../docs/decisions/0074-extractor-contract.md), [ADR 0075](../docs/decisions/0075-stems-as-content.md)) |
| [`hue_range.py`](hue_range.py) | The `colour` extractor's centroid hue limits taken from a set of recordings' `frames` entries, printed as a `--config` ([its README](extractors/colour/README.md#the-hue-range)) |
| [`media.py`](media.py), [`waveform.py`](waveform.py), [`derived.py`](derived.py) | Derived media, a standalone tool that never touches an archive; the waveform peaks' format and decode; and the layout ([ADR 0069](../docs/decisions/0069-derived-media.md)) |
| [`requirements.txt`](requirements.txt) | The libraries: numpy (detector, matchers, derived media) and scipy (numpy matcher) |
| [`requirements-match.txt`](requirements-match.txt) | The window matcher's interpreter: Essentia, numpy, and scipy for CPython 3.14, pinned by hash |
| `support.py`, `test_*.py`, `tests/` | Synthetic audio, scratch archives built through Door, and the tests |

The adapter and the probe use only Python's standard library; the detector
needs numpy and the numpy matcher numpy and scipy (`requirements.txt`). The
window matcher needs Essentia 2.1b6.dev1438, which exists only for CPython 3.14
on x86_64 Linux and macOS: `requirements-match.txt` pins it and everything
else `enrich/` needs for that interpreter, and the flake's `enrich-python` is
the same set. `enrich` and `capture` run on it. The probe and the matchers
need `ffmpeg` and `ffprobe`, the detector `ffmpeg`. The tests also need
World's test requirements, Door, and `archive` (`make test-python`, with
`ENRICH_PYTHON` naming the 3.14 interpreter).

## `enrich probe`

```sh
enrich/enrich.py probe --archive <dir> --artifact <object id> [--staging <annex>] [--dry-run]
```

Counts every audio stream of the Artifact's current revision and proposes one
`archive.claim/audio_stream` claim per counted stream, all made by one
`archive.claim/derivation_run` claim, in one Door batch. It prints one JSON
object and exits 0 when the claims were committed (`committed`), predicted by
a dry run (`predicted`), or already recorded (`unchanged`); 3 when no stream
could be counted exactly (`nothing_proposed`, with each stream's reason); 1 on
any failure, with `{"status": "failed", "detail": ...}` on stderr; and 2 when
the arguments are not understood.

- The Artifact's bytes are read from the archive's content worktree, where
  layout `archive/2` places them, and are checked against the digest and size
  the record states. Absent or altered bytes are refused.
- **The count is exact or not claimed.** `ffprobe` lists the streams, and
  `ffmpeg` decodes each one through the `ashowinfo` filter, which reports
  every decoded frame's rate, channel count, and sample count before any
  conversion. A stream is claimed only when every frame decoded at one rate
  and one channel count and the decoder logged no error (with frame checksums
  checked). A container's duration, a header's frame count, or a bitrate
  estimate is never used. A stream whose frames change rate, whose decoder
  reported damage (which ffmpeg would otherwise conceal), that decodes no
  frames, or that has more than 64 channels is refused with its reason.
- The decoder's log is read line by line as it arrives, and only a line that
  starts with the filter's prefix is a frame report, numbered in sequence.
  ffmpeg logs a file's tags in the same stream, indented, so a tag that
  imitates a frame report or an error neither adds samples nor refuses a
  count, and tags that are not UTF-8 are read.
- `codec` is ffprobe's codec name; `bits_per_sample` is stated for PCM codecs
  only.
- The run records `ffprobe`, `ffmpeg`, and the libav libraries `ffmpeg -version`
  reports, as installed at run time; the method is
  `archive-enrich/probe-decoded-frames` version 1, and the agent is
  `pkg:generic/archive-enrich-probe@0.1.0`.
- The run's parameters are the decoder invocation and the log levels that
  refuse a count, as canonical JSON. They change only with the method, so the
  first run proposes an Artifact holding them (which needs `--staging`) and
  later runs pin that Artifact.
- A rerun with the same agent, method, software, Artifact revision, and
  parameters as a recorded run answers `unchanged` before decoding anything
  and writes nothing (INV-5).

`probe.py <file>` runs the tool alone and prints its plain result; no archive
is involved.

## `enrich detect`

```sh
enrich/enrich.py detect --archive <dir> --artifact <object id> [--stream <claim id>] [--preset jam|show]
                        [--staging <annex>] [--dry-run]
```

Suggests spans of one audio stream of the Artifact's current revision and
proposes one `archive.claim/span_suggestions` claim, made by one
`archive.claim/derivation_run` claim, in one Door batch. It prints one JSON
object and exits 0 when the claim was committed (`committed`), predicted by a
dry run (`predicted`), or already recorded (`unchanged`); 1 on any failure,
with `{"status": "failed", "detail": ...}` on stderr; and 2 when the
arguments are not understood. There is no "nothing proposed": a stream that
decodes always gets at least one span, and one that cannot be analysed fails.

- **The stream comes from a claim.** The detector reads the
  `audio_stream` claim about exactly this Artifact revision (made by
  `enrich probe`), takes its `stream_index`, `sample_rate`, `channel_count`,
  and `sample_count`, and pins it in role `stream`; the Artifact is pinned in
  role `source`. With no such claim it refuses and says to probe first. With
  several (a file with two audio streams, or two probe runs) it refuses
  unless `--stream` names one; it never chooses silently.
- **The count must agree.** The stream is decoded once by `ffmpeg` to 32-bit
  float samples at its own rate and channel count. If the decoded frames are
  not whole frames of the claim's channel count, or their number differs from
  the claim's `sample_count`, the detection is refused: its spans would not
  be about that claim's samples.
- The run's `used` references pin the Artifact and the stream claim; its
  parameters are the preset as canonical JSON (`{"preset", "values",
  "decoder", "decode_arguments"}`), one Artifact per preset, admitted by the
  first run that uses it and reused afterwards. Its software is `ffmpeg`, the
  libav libraries it reports, and `numpy`. The method is
  `archive-enrich/detect-spans` version 1, and the agent is
  `pkg:generic/archive-enrich-detect@0.1.0`.
- A rerun with the same preset, software, Artifact revision, and stream claim
  answers `unchanged` before decoding anything and writes nothing (INV-5).
  Another preset is another run; both runs' suggestions remain, and neither
  supersedes the other (ADR 0066).

`detector.py <file> [--preset jam|show] [--stream-index n]` probes the file,
detects, and prints the plain result; no archive is involved.

### How spans are found

Everything is integer arithmetic on samples except the signal values
themselves. The decoded channels are averaged to one, and the stream is cut
into consecutive **hops** of `sample_rate * hop_ms // 1000` samples (800 at
8 kHz, 4,800 at 48 kHz for the presets' 100 ms); the last hop may be short.
Every preset duration is a whole number of hops, so every edge the detector
suggests is a hop boundary or the end of the stream. Per hop it measures the
mean square level (in dB relative to full scale; a hop with no energy reads
as -120 dB), a 12-class chroma (the power of a Hann-windowed FFT of the hop,
binned to the nearest pitch class between `chroma_min_hz` and
`chroma_max_hz`), and the spectral flatness (geometric over arithmetic mean
power).

The one signal set, all computed for every detection:

1. **Silence breaks.** Hops below `silence_threshold_db` form silent runs of
   at least `silence_min_ms`. Runs of at least `min_break_ms` are merged when
   separated by at most `merge_gap_ms`; each merged run breaks at its middle
   hop, if that leaves `min_segment_ms` after the previous break and before
   the end. At most `max_silence_breaks`, the longest first. Score: the run's
   length in seconds.
2. **Loudness transitions.** The level of consecutive `window_ms` windows; a
   point at the start of each window whose level differs from the previous
   window's by at least `min_transition_delta_db`, at least
   `min_transition_spacing_ms` after the previous point. At most
   `max_loudness_transitions`, the largest first. Score: the change in dB.
3. **Chroma novelty.** Chroma summed over `chroma_block_ms` blocks and
   normalised, then Foote novelty: a Gaussian-tapered checkerboard kernel of
   `chroma_kernel_blocks` blocks a side slid along the diagonal of the
   blocks' self-similarity, scaled to 0 to 1. A point at each block that
   reaches the `chroma_peak_percentile` of the curve and is its largest value
   within `chroma_min_spacing_ms` either side. A curve that is flat where the
   kernel fits has no peaks. Score: novelty times 60.

At most `max_review_points` points are kept, highest score first. Points are
clustered into **regions** while each is at most `review_cluster_gap_ms`
after the previous one. A region's edge is its strongest silence break, else
its strongest loudness transition, else its strongest novelty peak. Region
edges become boundaries in time order, each kept only when it leaves
`min_segment_ms` after the previous boundary and before the end.

**Quiet** is long low loudness relative to the recording's own floor, the
review application's notion of a long quiet stretch (60 s within 3 dB of
the session's 5th-percentile loudness). The floor is the
`quiet_floor_percentile` of the hop levels; a quiet hop is within
`quiet_margin_db` of the floor and below `silence_threshold_db`; a quiet run
is at least `quiet_min_ms` of quiet hops. Each quiet run is a span of its own
with exactly its edges. Its edges replace any boundary inside it or less than
`min_segment_ms` from it.

**Classes.** A quiet run's span is `quiet`. Every other span is classed by
the V2 detector's session-relative rule. For each span at least
`classify_min_ms` long, two features are computed from its hops: **key
confidence**, the best Pearson correlation of its mean chroma with the 24
rotated Krumhansl-Schmuckler major and minor profiles, and the mean
**spectral flatness**. Each feature is ranked among the recording's other
unquiet spans, from 0 to 1 exactly (equal values rank in span order; a lone
value has no rank), with flatness inverted. The mean of a span's ranks is
`music` at or above `rank_threshold_percent + rank_band_percent` percent,
`talk` at or below `rank_threshold_percent - rank_band_percent` percent, and
`unknown` in between, without a rank, or when the span is too short. The rule
is relative: in every recording with two or more classifiable spans, the
least tonal is ranked lowest, so one span of a recording of only music is
likely to be called `talk`.

**Failing loudly.** A signal that cannot be computed refuses the whole
detection and nothing is proposed: loudness when the stream fills fewer than
two windows, chroma novelty when it fills no more blocks than the kernel
spans or when the stream's rate leaves a pitch class without an FFT bin
between `chroma_min_hz` and `chroma_max_hz` (a rate of a few hundred hertz), and any signal whose values are not finite (samples that decode to
NaN, for example). More than 10,000 spans, which a claim cannot hold, is
refused too.

### How it differs from the V2 detector

The V2 system's detector produced float seconds for a preview cache. This one
produces evidence, so:

- **Samples, not seconds.** Every edge is an integer sample at the stream's
  own rate, on a hop boundary, measured against a counted stream claim. V2
  decoded loudness at 1,000 Hz, chroma at 22,050 Hz, and silences through
  `silencedetect`, then rounded seconds to six places.
- **One signal set.** Silence, loudness, and chroma novelty are computed by
  this tool from one decode with numpy. V2 combined `ffmpeg silencedetect`,
  its own PCM loudness, librosa's constant-Q chroma, and an optional
  spectral-balance signal, and classified with librosa features. Here chroma
  comes from an FFT of each hop instead of a constant-Q transform.
- **No silent skip.** V2 skipped chroma novelty when librosa was missing or
  failed, returned no novelty for a short file, and skipped loudness windows
  of digital silence; its output looked the same either way. Here every
  signal is computed or the detection fails.
- **Region edges by signal, not by raw score.** V2 took the edge from the
  highest-scoring point in a region, comparing seconds, decibels, and scaled
  novelty as if they were one unit. Here a silence break wins, then a
  loudness transition, then a novelty peak, so a cut falls in the middle of a
  silence when there is one.
- **Quiet spans.** V2 had no quiet class; the review application cut long
  quiet inside a span. Here a long quiet run is its own span.
- **Exact classification.** Ranks and their mean are exact fractions, so a
  span exactly on a threshold is classed by the rule as written, not by
  rounding. V2 rounded them to three places. The features being ranked are
  still floating point, so their order, and with it a rank, can differ in the
  last bit between numpy and FFT builds; that is why numpy's version is in
  the run's software. V2's `likely_song`, `likely_talk`, and `uncertain`
  are `music`, `talk`, and `unknown`.
- **Presets are evidence.** The `jam` and `show` values are V2's, written as
  integer milliseconds and decibels, and recorded as the run's parameters
  Artifact. V2's `strict-silence` preset, its spectral-balance values, and its
  1,000 Hz loudness rate are not carried over.

**The presets are not calibrated.** V2's values were tuned against V2's
signals; this detector's signals differ (FFT chroma, hop-level silence, the
quiet rule), and it has been tested only on synthetic audio of known
structure. Calibrating `jam` and `show` on real recordings is a follow-up for
the operator. A changed preset is a new parameters Artifact and a new run, so
earlier suggestions keep the values that made them.

## `enrich match`

```sh
enrich/enrich.py match --archive <dir> (--selection <id> | --occurrence <id>) --limit <decimal>
                 [--staging <annex>] [--dry-run]
```

Compares one `archive.claim/audio_selection` version 2 claim, or one
`archive.claim/song_occurrence` version 2 claim, with the occurrences linked
to each Work, and proposes at most one `archive.claim/work_match` version 2
claim per Work, all made by one derivation run in one Door batch
([ADR 0067](../docs/decisions/0067-matches-and-alignments-pin-runs.md),
[ADR 0047](../docs/decisions/0047-v2-audio-route-source-rules.md) rule 5). It
prints one JSON object with every Work's scores and exits as `enrich probe`
does: 0 when matches were committed, predicted, or already recorded; 3 when
no Work's best score is within the limit (`nothing_proposed`); 1 on any
failure; and 2 when the arguments are not understood.

- **The corpus** is read from `archive export`, never from record files. It is
  every head `archive.claim/performance_work` version 3 claim and the song
  occurrence it links, grouped by the head revision of its Work. Nothing is
  chosen as current: every head link counts. A link is skipped, with its
  reason in the answer, when it pins a superseded occurrence revision, when
  its occurrence is the compared one or shares one of its selections (a
  recording is not evidence about itself), or when its Work's head is not a
  `musical_work` Entity. A corpus left empty is a failure, not a quiet "no
  match".
- **The audio** of an occurrence is its first selection in the contract's
  canonical order, and of a selection its first track: the tracks of one
  selection capture the same moment. Each span is decoded from the pinned
  Artifact's locally present content (checked against its digest) at the
  file's own rate, trimmed in samples, downmixed to mono, and resampled. A
  span whose rate is not the file's, that runs past the audio, or whose audio
  is shorter than the method's minimum refuses the whole run.
- **Only the best match per Work** is proposed, and only when its score is at
  or below `--limit`. Its `corpus` pins every occurrence of that Work that was
  compared, since the score is their minimum; `work` pins the Work's head
  revision; `selection` or `occurrence` pins the compared claim; `parameters`
  pins the run's parameters Artifact; and `run` pins the run. The payload is
  exactly the method, the score as decimal text, the scale, and
  `lower_is_closer`. A match never establishes a Work (ADR 0040).
- **The run** reads (`used`) the compared claim and its selection, every
  corpus link, occurrence, selection, and Work, and every Artifact whose bytes
  were decoded. Its software is ffprobe, ffmpeg, the libav libraries, numpy,
  and scipy, as installed at run time. A rerun with the same inputs, software,
  and parameters answers `unchanged` before decoding anything.
- **The parameters** are every setting of the method, the decoder invocation,
  and the limit, as canonical JSON with decimal text for fractions. A
  different limit is a different configuration and so a different run.

### The method, and its thresholds

The method is `archive-enrich/chroma-oti-qmax` version 1, made by
`pkg:generic/archive-enrich-match@0.1.0`, with scale
`archive-enrich/chroma-oti-qmax/sqrt-min-length-over-qmax`. It follows the
cover-song measure of Serrà, Serra, and Andrzejak (2009) in numpy and scipy:
pitch-class profiles of spectral peaks, the optimal transposition index, a
binary cross-recurrence matrix of stacked frames that marks mutual nearest
neighbours, and the Qmax local alignment with gap penalties. Each profile is
rounded to integer units (`profile_units`) before the transposition, so the
distances, the marks, and Qmax are exact integer arithmetic: they depend on
no linear-algebra library (the BLAS behind numpy differs between a pip wheel
and the flake), thread count, or summation order, none of which a run
records. The profiles themselves are floating-point and depend on the numpy
and scipy versions the run records and, in the last bits, on the machine; a
value within a rounding error of half a unit can round the other way elsewhere.
The score is `sqrt(min(N_query, N_reference)) / Qmax`, rounded
half to even to six places; lower is closer, and a pair with no aligned path
has no score. `match.py <query> <reference>...` scores whole files with no
archive involved.

This method is its own implementation under its own identifier, and its
scores are not on the V2 system's scale: the V2 thresholds do not apply.
`enrich match-windows` uses Essentia's profiles instead, with thresholds
fitted on the archive's own accepted occurrences
([ADR 0071](../docs/decisions/0071-essentia-cover-similarity.md)).
**The operator must calibrate `--limit` on real recordings** (known same-song
and different-song pairs from the archive) before trusting a proposal; no
default limit exists. The tests show only that, on synthetic tunes, the same
melody in another key scores lower than a different melody, and that scores
are deterministic.

## `enrich match-windows`

```sh
enrich/enrich.py match-windows --archive <dir> --suggestions <object id> [--jobs <n>]
                 [--staging <annex>] [--dry-run | --regenerate]
```

Searches the whole recording behind one `archive.claim/span_suggestions`
claim for the Works of the corpus. Each suggested window becomes an
`archive.claim/work_match` version 3 claim
([ADR 0071](../docs/decisions/0071-essentia-cover-similarity.md) and
[ADR 0072](../docs/decisions/0072-windowed-work-matches.md), as their
amendments of 2026-10-18, which the operator accepted that day). All claims are made by one derivation run in one Door batch.

It prints one JSON object. The object holds every kept window with its
closest occurrences, the path that decided it, and whether it was proposed,
and the number of hits and regions behind them. For each window it also keeps
what is not claimed:

- `within`: every Work whose closest occurrence is within the deciding path's
  limit (0.118, or 0.08 for a confirmed near tie), closest first, with that
  occurrence and its score. The claimed Work is first; the others are never
  claimed;
- `work_margin`: how far the second Work is behind the first on that path;
- for a near tie, `quarter_rate_within` and `quarter_rate_margin`, the
  quarter-rate comparison that sent it to the exact distance, and
  `compared_exactly`, the occurrences the exact distance compared.

This answer is regenerable output of the run, not evidence. `capture import`
keeps each window in its report. `--regenerate` searches again even when an
equivalent run is recorded and prints the same answer, with status
`regenerated` and that run's pin, proposing nothing.

The exit status is the same as for `enrich match`:

- 0 when matches were committed, predicted, already recorded, or regenerated;
- 3 when no window was suggested, the corpus is empty, or no corpus occurrence
  is long enough (`nothing_proposed`, with the reason);
- 1 on any failure;
- 2 when the arguments are not understood.

The capture import runs it last.

- **The corpus** is the corpus `enrich match` uses, less every occurrence
  whose selections take audio from the suggestions' source, or from any part
  of any source in its session, as head `session_parts` claims record them.
  Another microphone on the same rehearsal is not evidence about it. Each
  occurrence is measured once, from its first selection's first track. An
  occurrence too short to compare is skipped, with its reason. Any other
  failure to read one refuses the run.
- **The recording is measured once, whole.** The pinned stream is decoded from
  sample 0 to its `sample_count`, in blocks, and turned into HPCP profiles.
  These take about 1.9 MB per recording-hour, and memory does not grow with
  the recording's length. Every later step reads a window as a slice of these
  profiles. The one exception is an exact confirmation, which decodes its
  window afresh.
- **The candidate pass** compares every corpus occurrence along the recording
  at a quarter of the frame rate:
  - it uses chunks 2 occurrence-lengths long, one starting every
    occurrence-length;
  - it traces up to 2 alignments per chunk;
  - an alignment within 0.20 is a hit.

  `--jobs` processes share the occurrences. The answer does not depend on how
  many there are.
- **Regions and windows.** Overlapping hits gather into regions, and each
  region keeps its 5 closest Works. A region snaps to the group of `music` and
  `unknown` spans it overlaps most. A `talk` or `quiet` span between them does
  not split the group. That gives an outer variant and an inner one, and
  either is dropped when the quarter-rate distance cannot compare it: when
  its profiles make 10 or fewer quarter-rate frames, under 44 frames or about
  3.9 seconds. Each window's edges are span edges, which Door checks.
- **Claim rounds.** Windows are compared with the occurrences of their
  candidate Works, which are the region's Works and up to 8 Works hit inside
  the window. The closest window is kept and claims its spans, and the other
  regions snap again without those spans. Kept windows never overlap in spans.
- **The decision.** Each kept window is compared with every corpus occurrence
  by the quarter-rate distance. It is suggested when its closest occurrence's
  score, rounded to four places, is at or below 0.118.
  - **A near tie** is a suggested window whose closest Work beats the next by
    less than 0.02, compared on the unrounded distances. It is decided again by Essentia's exact distance, on
    its audio decoded afresh. It is compared with its candidate Works'
    occurrences and with the half of the corpus closest by mean chroma, and is
    suggested only at or below 0.08.
  - No other window is ever compared exactly.
- **What is proposed.** A suggested window gets one claim, for its closest
  Work:
  - `suggestions` pins the suggestions;
  - `work` pins the Work's head;
  - `corpus` pins that Work's occurrences that aligned on the path that decided
    it;
  - `parameters` and `run` are as for every match.

  The payload adds `sample_rate` and `window`. Its score and scale are those of
  the deciding path: the quarter-rate scale, or Essentia's for a confirmed
  near tie. Other Works within the distance are not claimed; the answer keeps
  them, as above.
- **The run** reads the suggestions, their source and stream, and everything
  `enrich match` reads for the corpus. Its software is ffprobe, ffmpeg, the
  libav libraries, Essentia, numpy, the BLAS library numpy uses, and how that
  library runs (`blas-runtime`): the kernel it chose for this CPU (OpenBLAS)
  or the CPU model (Accelerate), and one thread. `cover.py` pins BLAS to one
  thread in every process before numpy loads it, and refuses to name a run
  whose numpy loaded first. A rerun
  with the same inputs, software, and parameters answers `unchanged` before
  decoding anything.
- **Cost.** On the calibration workset, the search took 0.59 to 0.64
  CPU-hours for 19.8 recording-hours against about 92 occurrences, in three
  runs of 10 to 11 minutes on 4 processes with one BLAS thread each, at the
  first margin of 0.01475 with 10 near ties. The margin of 0.02 confirms 11,
  so about 0.61 to 0.66 CPU-hours. That is roughly 111 to 120 CPU-seconds per
  recording-hour: 73 for the quarter-rate passes, the rest for the exact
  comparisons of the near ties, about 71 to 77 CPU-seconds each. Measuring
  the recordings whole adds about 0.06 CPU-hours, so the whole command costs
  about 0.67 to 0.72 CPU-hours there. It
  grows with the size of the corpus. `--jobs` shares the candidate pass, the
  largest part, and a near tie's exact comparisons; claim rounds and
  decisions run in one process.
- **Memory.** Measuring a recording whole holds its profiles and one block of
  audio: a 1.6-hour, 48 kHz stereo file peaked at about 250 MB. The search
  itself stays under about 0.8 GB resident for a 1.55-hour recording, but a
  near tie's exact comparisons raised that recording's peak to about 2.3 GB.

### The method

The method is `archive-enrich/whole-recording-serra09` version 1, made by
`pkg:generic/archive-enrich-cover@0.2.0`. Every value below is in the run's
parameters (`cover.parameters()`).

The profiles are the V2 system's measure, setting for setting:

1. ffmpeg decodes to mono 16-bit PCM at 22,050 Hz, scaled by 1/32,768.
2. Each 4,096-sample frame, with a hop of 2,048, passes through a
   Blackman-Harris 62 dB window, the spectrum, spectral peaks (40 to
   5,000 Hz), and HPCP.

The **quarter-rate distance** decides most windows. Its scale is
`archive-enrich/whole-recording-serra09/quarter-rate-distance`. It works as
follows:

1. Average every 4 frames, and divide each averaged frame by its largest bin.
2. Rotate the occurrence by the key shift that best matches the two mean
   frames.
3. Build stacks of 9 frames, and compare them by float32 squared distances
   from one matrix product.
4. Set a cell when its distance is within both its row's and its column's
   order statistic at int(0.095 m) − 1.
5. Score with serra09.
6. The distance is √(smaller dimension) / best score. That is the smaller of
   the two directions' distances.
7. Round half to even to four places; lower is closer.

Its low bits depend on the BLAS library, which the run records.

The **exact distance** decides near ties. Its scale is
`archive-enrich/essentia-serra09/smaller-asymmetric-distance`. It is
Essentia's `ChromaCrossSimilarity` and `CoverSongSimilarity`, run in both
directions with the smaller distance kept, as the bit-exact port in `cover.py`
reproduces them.

Only Essentia 2.1b6.dev1438 is accepted, because it measures the profiles that
every threshold was fitted on.

The thresholds of 0.20, 0.118, and 0.02 were chosen on the calibration
workset: 17 recordings and 98 accepted occurrences. The exact distance's 0.08
is the V2 system's: one false positive in 2,779 controlled pairs of the
operator's own recordings. None of these thresholds has been checked out of
sample, and the V2 system's release threshold of about 0.11 is not used
([open questions](../docs/open-questions.md)).

`cover.py <query> <reference>...` still prints the exact distances of whole
files, under `archive-enrich/essentia-serra09`, with no archive involved.

### Checking the thresholds

`calibrate_windows.py` checks the thresholds against an archive's accepted
occurrences before its suggestions are trusted. It proposes nothing.

- **The searched recordings.** Every recording that holds an accepted
  occurrence is searched exactly as `match-windows` would search it, with
  every other accepted occurrence outside its session as the corpus.
- **The judgments.** Each kept window is judged against what was accepted on
  that recording. A window is correct when its closest Work is the Work of an
  accepted occurrence it overlaps, wrong when it overlaps only accepted
  occurrences of other Works, and unaccepted when it overlaps none. Each count
  is given under two rules. The lenient rule counts any overlap. The strict
  rule counts only accepted occurrences that cover more than half of the
  window, and an accepted occurrence counts as suggested only through a
  correct suggested window that it covers more than half of, so one window
  never finds two occurrences.
- **What is reported.** The report gives reach, how many accepted occurrences
  were suggested, and how many were never touched.

`all` runs everything in one process. The other subcommands split the work
across machines:

- `measure` runs where the audio is. It writes a workset holding the corpus's
  profiles and each recording's whole-recording profiles, but no audio, and
  prints the workset's digest and the source indices to hand out.
- `search --source-index i` searches one recording from the workset alone.
  This is the unit of work handed out, one per recording.
- A near tie needs its window decoded afresh, which a workset cannot do. In
  that case `search` reports the window as needing measurement. `measure-windows`,
  run where the audio is, measures every window the results ask for. A second
  `search` with `--windows` then decides them. That second round repeats the
  search of each recording with a near tie: on the calibration workset, about
  1,020 CPU-seconds on an Apple M2 for 9 recordings.
  The windows file names the workset it was measured for, and `search`
  refuses one measured for another.
- `judge` refuses a recording without a result or with two, any result that
  still needs a window, and any workset in the earlier, per-window format.

A result names the software it ran with. Quarter-rate distances can differ in
their low bits between BLAS libraries and kernels, and a run with other
software is another run, so `judge` refuses results whose software differs.
`search` checks only the Essentia and numpy versions against the workset's, so
split a workset only across machines whose software agrees.

The workset and the windows file are regenerable: they are measured again
from the audio. They are derived from the operator's recordings, so they stay
outside this repository.

## The adapter interface

A new tool (a detector, a matcher) uses the adapter like this:

```python
import adapter

reader = adapter.ArchiveReader(archive_root, archive="archive", env=env)
source = reader.head(artifact_object_id)            # the current revision, as exported
used = (adapter.Pin("used", source["object_id"], source["revision_id"]),)
started = adapter.now()
... run the tool on the file ...
result = adapter.RunResult(
    agent="pkg:generic/<tool>@<version>",           # the tool's Package URL (D-7)
    method_id="<method>", method_version="<n>",
    software=(("<program>", "<version detected now>"), ...),
    started_at=started, ended_at=adapter.now(),
    used=used,                                      # every revision the run read
    outputs=(adapter.Output(("archive.claim/<kind>", "<version>"), "<token>", payload,
                            (adapter.Pin("<role>", object_id, revision_id), ...)), ...),
    parameters={...} or None,                       # the configuration, if any
)
if reader.equivalent_run(result) is None:           # optional quiet-history check
    admission = adapter.propose(reader, result, door="door", staging=adapter.Staging(path, git, annex, env),
                                dry_run=False, env=env)
```

- **`ArchiveReader(root, archive, env)`** runs `archive export` and refuses an
  incomplete or failed one. It offers `head(object_id)`, `citation(contract)`
  (the installed bundle's digest, refused when the archive's specification
  does not install it), `artifact_with_digest(digest)`, and
  `equivalent_run(result)`, which finds a recorded run of the same agent,
  method, software, `used` revisions, and parameter bytes that kept an output.
  Compare before writing: a tool that checks this before its expensive step
  keeps a rerun quiet and cheap. `refresh(archive, env)` adds what was
  committed since the reader's snapshot from `archive export --since` (ADR
  0055) instead of exporting every record again, and refuses an archive
  whose identifier or specification changed meanwhile. Every command takes
  `--export <file>`, an export a caller already holds: it must name the
  archive's current commit, or the command refuses it, so a stale export is
  never proposed against.
- **`RunResult`** is what the tool hands over. `software` lists the programs
  and libraries the output depends on, in the order the tool reports them,
  detected when the run happens. Instants must be timezone-aware; they are
  written in UTC with `Z`. Thresholds, presets, and prompts belong in
  `parameters`, never in an output payload.
- **`Output(contract, name, payload, refs)`** is one claim the run made. The
  adapter cites the contract's installed bundle, names the claim
  `<run name>-<name>` in the contract's namespace, and adds the `run` pin;
  `refs` are the output's other pins, and must not include `run`. They pin
  revisions already committed: an output cannot pin another output of the
  same run, because those revisions exist only inside `propose`. A detector
  whose suggestions pin a stream claim (ADR 0066) therefore pins one an
  earlier probe run committed, and a tool that needs both proposes them as
  two runs. An output that must pin the run's parameters Artifact (a match,
  whose contract has a `parameters` role) names that role in
  `parameters_role`, and the adapter adds the pin, whether the Artifact is
  reused or new in the same batch.
- **`propose(reader, result, door, staging, dry_run, by, env)`** checks the
  proposer's obligations of D-15 (a Package URL agent, at least one output, a
  run that does not end before it starts, `used` pins to revisions in the
  archive) and builds one batch: the parameters Artifact when it is new, the
  run (`run-<uuidv4>` in `archive.derivation/run`), and every output. Each
  revision is computed in advance, so the outputs can pin the run, and Door's
  answer must equal them. It returns an `Admission` with the run, the
  outputs, the parameters (`reused` or not), and the revision identifiers.
- **Parameters** are written as RFC 8785 canonical JSON (`canonical()`). When
  an Artifact's current revision holds exactly those bytes, the run pins
  that revision; a superseded revision is never reused. Otherwise the bytes are added to the `Staging` repository (a
  git-annex repository, created when absent), which is configured in the
  archive as a Git remote (an existing remote with the same path, or
  `enrich-staging`), and declared as content for Door to fetch and verify
  ([ADR 0023](../docs/decisions/0023-admission-and-staging.md)). Without
  `staging` a new parameters Artifact is refused.
- **`Staging(path, git, annex, env, remote="enrich-staging")`** is also how
  the [capture importer](../capture/README.md) stages recordings:
  `stage_file(source, digest, size, filename)` copies a file in without
  holding it in memory and only reads the source; on a file system that can
  share extents (a Btrfs reflink, Linux's `FICLONE`) it clones the file
  instead and writes no data, and it copies only when the file system
  answers that it cannot clone. `key_for(digest, size,
  filename)` predicts its key. The copy is hashed before git-annex adds it,
  and a copy that is not the stated digest and size is removed and refused,
  so a file that changed after it was hashed is never offered as the hashed
  content and never left staged under its name; a staged key that is not the
  stated one is refused too. `remote` names the remote added when
  no remote has the repository's path.
- **A dry run** stages nothing and configures no remote; Door predicts the
  revisions without fetching anything.
- `AdapterError` carries every refusal, Door's included (an answer that is
  not the planned status and revisions is refused too); a refused batch
  leaves the archive unchanged.
- `equivalent_run` counts a recorded run only when some claim pins it as
  `run`: a run that kept no output (which D-15 forbids proposing, but Door
  cannot detect) never makes a rerun quiet.

The adapter proposes only through `door admit`; `door serve` takes the same
proposal directory, so a long-running caller can switch without changing the
batch.

## Derived media

[`media.py`](media.py) makes the waveform peaks and the listening copy of one
audio file ([ADR 0069](../docs/decisions/0069-derived-media.md)):

```sh
enrich/media.py --root /path/to/derived-media --digest sha256:<hex> /path/to/recording.wav
```

`--kind peaks` or `--kind listen` makes only that kind; peaks take a decode,
the listening copy an encode that takes most of the time, so an importer
makes every recording's peaks first.

Both are regenerable, never admitted. They go under the derived-media root at
`sha256/<ab>/<cd>/<hex>/<name>`, where `<hex>` is the content's SHA-256 and
`<name>` carries the method version ([`derived.py`](derived.py) is the layout,
which the [relay](../relay/README.md) serves from):

| Name | What |
| --- | --- |
| `peaks-1.dat` | an audiowaveform binary file, version 2, 8-bit: per pixel, a signed minimum and maximum for every channel, from the full-resolution decode, with `samples_per_pixel` the sample rate over 100 rounded half to even |
| `listen-1.mp4` | AAC-LC at 192 kb/s in MP4 with its index first, downmixed to stereo when the source has more than two channels |

- The file's SHA-256 must be the digest given; the digest names the output.
- The file must hold exactly one audio stream. More than one is refused,
  because which one is the recording is not the tool's to guess.
- Each output is written to a temporary file beside it and renamed into
  place, so a reader sees all of it or none. A rerun on complete outputs
  decodes and writes nothing; an incomplete output is made again.
- The root must already exist. Files are made readable by their owner only:
  a derivative carries the recording, so run the relay as the same user.
- 8-bit values follow audiowaveform's rule for 16-bit samples: a full-scale
  sample `x` becomes `floor(x * 128)`, clipped to -128..127.

It prints `{"digest", "outputs": {kind: {"path", "status"}}}`, where `status`
is `written` or `present`; a failure prints `{"status": "failed", "detail"}`
to stderr and exits 1. It needs `ffmpeg` and `ffprobe` (`--ffmpeg` and
`--ffprobe` name others) and numpy ([`requirements.txt`](requirements.txt)).

## `extract.py`: the feature bank

```sh
enrich/extract.py list
enrich/extract.py run <extractor|all> --content <file> --bank <dir> [--digest sha256:<hex>] [--jobs n]
                  [--from <extractor>/<output>] [--config <file>] [--program name=path]...
                  [--extractors <dir>]... [--dry-run]
```

Runs extractors on one file and files their features in the feature bank,
regenerable state laid out by content digest beside derived media (proposed
[ADR 0073](../docs/decisions/0073-feature-bank.md) and
[ADR 0074](../docs/decisions/0074-extractor-contract.md)). It never
touches an archive and proposes nothing. Each extractor is a directory under
[`extractors/`](extractors/frames/extractor.json) with a manifest and a
program in any language; the runner orders an extractor after the features
it needs, runs only what is missing, checks every output against the
manifest, and renames a complete entry into place. It prints each extractor's
`hit`, `ran`, `failed`, `blocked`, or `skipped` and exits 0 only when none
failed or was blocked. `bank.py` reads entries back
(`bank.Bank(root).entry(digest, "frames").series("hpcp")`), and
`cover.py --bank <dir>` reads whole files' pitch-class profiles from it.

Versions, needs, and outputs by kind are listed together in
[the shipped extractors](../docs/feature-bank.md#the-shipped-extractors).

| Extractor | Language | Writes |
|---|---|---|
| `frames` | Python, Essentia 2.1b6.dev1438 | on one grid of 4,096-sample frames every 2,048 at 22,050 Hz: `hpcp` (the bits `cover.py` measures), `rms_db`, `centroid`, `rolloff`, `flatness`, `flux`, `onset_hfc`, `mfcc`, `melbands`; scalars `samples`, `seconds` |
| `loudness-r128` | POSIX shell, ffmpeg | `integrated`: EBU R 128 integrated loudness in LUFS |
| `rhythm` | Python, Essentia 2.1b6.dev1438 | from its own 44,100 Hz decode (both algorithms need that rate and hops finer than `frames`), in 300 s chunks overlapping by 30 s: events `beats` (RhythmExtractor2013 multifeature, moved onto the onsets' time base, kept only on an onset or between onsets) and `onsets` (OnsetRate, a few ms before each attack); on a 1 s grid of 10 s windows `tempo`, `onset_count`, `onset_deviation` (ms from the beat grid divided into 4), `offgrid`; per chunk `chunk_bpm`, `chunk_confidence`; scalars `bpm` (the tracker's median, which can be a level never played), `confidence`, `onset_rate`, `seconds`. From the beats and its own spectral flux and chroma ([`meter.py`](extractors/rhythm/meter.py)): on the 1 s grid `primary_tempo` (the level among 1:2, 2:3, 3:4, 1:1, 4:3, 3:2, 2:1 of the tracker's with the most onset support, octaves settled by a preference near `prior_bpm` and one for the tracker's own octave; each take on its own), `tempo_support` (7 columns: the support of those relations of the primary) and `downbeat_margin` (how clearly the bar lines are placed); events `pulse` (the primary level's beats, valued by place in the bar, 0 where that is a guess) and `downbeats` (valued by the bar's beats, labelled by its meter, only where `downbeat_margin` reaches its setting: on dense commercial music few); spans `tempo_spans` (one level and one meter: `4/4`, `3/4`, `5/4`, `7/4`, `12/8`, `9/8`, ...); events `tempo_changes` (`meter` where the beat or a subdivision persists, `tempo` otherwise); scalars `primary_bpm`, `meter` |
| `tonal` | Python, Essentia 2.1b6.dev1438, numpy, ffmpeg | from `frames/rms_db`, plus two decodes that repeat `frames`' framing for its spectral peaks (the first for the tuning, the second for profiles referenced to it): scalars `tuning_frequency`, `tuning_cents`, `tuning_confidence` (the confidence-weighted median of 20 s windows' tunings, each the densest stretch of a histogram of the loud peaks' distance from the tempered grid, mains hum left out), `tuning_window_count`, `drift_spread_cents`, `detuned`, `collection` (the note collection, such as `Eb major scale`), `collection_margin`, `key`, `mode` (ionian to locrian, harmonic or melodic minor), `scale` (major or minor, as before), `scale_notes`, `key_strength`, `key_margin`, `tonic_margin`, `tonic_named` (whether the tonic leads its collection's other modes by 0.03; if not, read `collection`); series `tuning_windows` (grid `tuning`); spans `tuning_drift`, `detuned_spans`, `keys`, `key_regions` (a timeline of note collections, the tonic and mode named where they lead); on the `frames` grid, `pitched`, `dissonance`, and `chroma` and `bass_chroma` (twelve-bin profiles of 100-5,000 Hz and 40-250 Hz, referenced to the local tuning). Version 3 moved chords to `chords` (since retired); version 4 leaves mains hum out and names a tonic only where it leads |
| `structure` | Python, numpy; decodes nothing | from `frames` and `rhythm`: spans `playing`, `tuning` (held single pitches between the playing, labelled by pitch class), `count_ins` (three or more regular onsets before playing), `false_starts` (playing of at most 30 s and a restart, with the openings' similarity), `endings` (`stop`, `fade`, or `abrupt`), `sections` (self-similarity novelty peaks, labelled `A`, `B`, ...), `loops` (a pitch-class period repeated three or more times); series `novelty` a block of about 1 s; scalars `floor_db`, `reference_db`, `threshold_db`, and `floor` (whether the recording has a noise floor, as a rehearsal's room noise is: steady, onset-free quiet at least 26 dB under the playing, or unpitched quiet, a flat pitch-class profile at least 12 dB under it, struck or not; a master, whose quiet is pitched music, has none, so all of it is playing and it has no tuning, count-ins, or false starts); every span but `playing` with a confidence. Not claims: span suggestions are the span detector's |
| `quality` | Python, Essentia 2.1b6.dev1438, ffmpeg | how well a recording was captured, from one more decode at the stream's own rate with every channel: EBU R 128 `integrated`, `loudness_range`, and `true_peak`; `sample_peak`, `full_scale_samples`, `clipped_seconds`, `dc_offset`, `whole_correlation`, `anti_phase`, `false_stereo`, `noise_floor`, `snr`, `bandwidth`, `band_limited`; per channel, `clipping` (flat tops at full scale, or long ones at the recording's own peak), `overs` (0 dBTP reached between samples, 4x oversampled), `gaps`, `noise_bursts` (spans) and `clicks`, `discontinuities` (events), each of the last three kept only where it stands out from the transients around it or (clicks and discontinuities) stands alone among the steps of the 150 ms around it, so that drums and limiters on a finished master are not defects while crackle still is; `hum` (spans: narrow, steady peaks at 50 or 60 Hz and their harmonics that persist through the quiet or sound in it); on the `frames` grid, `correlation` and `peak_db` |
| `melody` | Python, Essentia 2.1b6.dev1438 | on a grid of 2,048-sample frames every 128 (the `hop_size` stride) at 44,100 Hz: `pitch` (MELODIA, in 2-cent bins), `confidence`, `voiced`, `midi`, `cents`, `level_db`; `notes` spans; scalars `voiced_fraction`, `melodia_voiced_fraction`, `loud_db`, `gate_db`, `reference_hz`, `samples`. The predominant melody of whatever audio is the content (`--from stems/vocals` for the sung line); `voiced` is MELODIA's decision kept where a contour is mostly above a gate relative to the recording's loud level |
| `colour` | Python, numpy, ffmpeg | for each waveform pixel of [`media.py`](media.py)'s derived-media peaks (about 10 ms; the peaks stay derived media and are not copied into the bank): series `colour` (`u8` red, green, blue, on grid `peaks`) coloured from `frames/centroid` (hue between limits that [`hue_range.py`](hue_range.py) can take from a set of recordings) or `frames/hpcp` (hue) and `frames/rms_db` (lightness), interpolated to each pixel's centre; file `palette` (the mapping and legend stops, JSON, fields in [its README](extractors/colour/README.md)); file `preview` (a PNG of the whole recording). The mapping is its parameters |
| `stems` | Python under `${demucs-python}` (Demucs 4.1.0, PyTorch), ffmpeg | `vocals`, `drums`, `bass`, `other`: 44.1 kHz stereo 16-bit FLAC from pinned htdemucs weights; `levels` (each stem's RMS every 4,096 samples); each stem's `_rms_db`, `_peak_db`, `_clamped`; `mix_rms_db`, `samples`, `seconds` ([ADR 0075](../docs/decisions/0075-stems-as-content.md)) |
| `beats-beatthis` | Python under `${beatthis-python}` (beat_this 1.1.0, PyTorch), with the checkpoint `${beatthis-checkpoint}` | from its own decode at the file's rate (soundfile; the model resamples to 22,050 Hz mono): events `beats` and `downbeats` from Beat This's pinned `final0` checkpoint with its minimal postprocessor, at the model's own metrical level (nothing asserts a meter, and the downbeats' spacing is not one); scalars `beat_count`, `downbeat_count`, `median_bpm` (60 over the median beat interval). On a 69-minute album, 0.20 CPU-seconds a second of audio and 0.76 GB at most |
| `chords-jiang` | Python under `${jiang-python}` (PyTorch, librosa), from the recogniser's checkout `${jiang-repo}` | from its own decode: spans `chords` (the HMM-decoded labels of Jiang et al.'s large-vocabulary recogniser in Harte spelling, `N` for none, each valued by its label's weight), file `readings` (JSON: per span, the decoded label and the three most probable templates with weights, each a share of the whole template set, so a span's three sum to at most 1), scalars `chord_count` and `decoded_in_top3`. On the same album, 0.17 CPU-seconds a second and 1.6 GB at most |
| `chords-chordino` | Python under `${chordino-python}` (the vamp host, chord-extractor 0.1.3's Chordino build, librosa) | from its own 44,100 Hz mono decode: spans `chords` (Chordino's simple chord labels in its own spelling, such as `Am7` or `F/A`, `N` for none, tiling the decoded length); scalars `chord_count`, `seconds`. Not learned: a second opinion beside `chords-jiang`. On the same album, 0.042 CPU-seconds a second and 0.56 GB at most |

`--from stems/vocals` measures a stem as content: every extractor runs on it
unchanged, keyed by the stem's own digest and filed with the recording, after
the stems are made if they are missing. `stems` runs only where the operator
configures Demucs's interpreter (`--program demucs-python=<path>`, or
`programs` in `--config`), and the learned beat and chord extractors only
where their interpreters and model files are configured
(`beatthis-python`, `beatthis-checkpoint`, `jiang-python`, `jiang-repo`,
`chordino-python`); `run all` elsewhere skips them and says so. Each checks
its model files against the digests its parameters pin and refuses others.
The Essentia `chords` extractor is retired: its entries stay readable
([retired extractors](../docs/feature-bank.md#retired-extractors)).

The format is [docs/feature-bank.md](../docs/feature-bank.md); adding an
extractor is [docs/adding-an-extractor.md](../docs/adding-an-extractor.md).
Entries are readable by their owner only: a stem carries the recording.

## Test

```sh
python -m pip install -r enrich/requirements.txt
python -m unittest discover -s enrich/tests
python -m unittest discover -s enrich -p 'test_*.py'
```

The tests make their audio with numpy and ffmpeg's lavfi sources. Without
ffmpeg the derived-media tests are skipped, unless
`ARCHIVE_REQUIRE_INTEGRATION` is set.
