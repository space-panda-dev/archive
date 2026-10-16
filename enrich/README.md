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
| [`enrich.py`](enrich.py) | The `enrich` command that runs a tool against an archive's Artifact |
| [`media.py`](media.py), [`derived.py`](derived.py) | Derived media, a standalone tool that never touches an archive, and its layout ([ADR 0069](../docs/decisions/0069-derived-media.md)) |
| `support.py`, `test_*.py`, `tests/` | Synthetic audio, scratch archives built through Door, and the tests |

The adapter and the probe use only Python's standard library; the detector
also needs numpy ([`requirements.txt`](requirements.txt)). The probe needs
`ffmpeg` and `ffprobe`, the detector `ffmpeg`. The tests also need World's
test requirements, numpy, Door, and `archive` (`make test-python`).

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
  keeps a rerun quiet and cheap.
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
  two runs.
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
  holding it in memory and only reads the source, and `key_for(digest, size,
  filename)` predicts its key. A staged key that is not the key of the stated
  digest and size is refused, so a file that changed after it was hashed is
  never offered as the hashed content. `remote` names the remote added when
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

## Test

```sh
python -m pip install -r enrich/requirements.txt
python -m unittest discover -s enrich/tests
python -m unittest discover -s enrich -p 'test_*.py'
```

The tests make their audio with numpy and ffmpeg's lavfi sources. Without
ffmpeg they are skipped, unless `ARCHIVE_REQUIRE_INTEGRATION` is set.
