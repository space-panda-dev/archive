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
| [`match.py`](match.py) | The work matcher, a standalone tool ([ADR 0067](../docs/decisions/0067-matches-and-alignments-pin-runs.md)) |
| [`enrich.py`](enrich.py) | The `enrich` command that runs a tool against an archive's records |
| [`requirements.txt`](requirements.txt) | The matcher's libraries, numpy and scipy |
| `support.py`, `test_*.py` | Synthetic audio, scratch archives built through Door, and the tests |

The adapter and the probe use only Python's standard library; the matcher
needs numpy and scipy (`requirements.txt`, and the flake's Python), and both
tools need `ffmpeg` and `ffprobe`. The tests also need World's test
requirements, Door, and `archive` (`make test-python`).

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
neighbours, and the Qmax local alignment with gap penalties, computed in exact
integer units. The score is `sqrt(min(N_query, N_reference)) / Qmax`, rounded
half to even to six places; lower is closer, and a pair with no aligned path
has no score. `match.py <query> <reference>...` scores whole files with no
archive involved.

The V2 system computed the same kind of measure with Essentia, whose Python
bindings are not in nixpkgs and whose published wheels are pre-releases for
some platforms only, so they cannot be obtained reproducibly for both the
flake and a macOS virtual environment. This method is therefore its own
implementation under its own identifier, and its scores are not on the V2
system's scale: the V2 thresholds (0.08 and 0.18) do not apply.
**The operator must calibrate `--limit` on real recordings** (known same-song
and different-song pairs from the archive) before trusting a proposal; no
default limit exists. The tests show only that, on synthetic tunes, the same
melody in another key scores lower than a different melody, and that scores
are deterministic.

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
