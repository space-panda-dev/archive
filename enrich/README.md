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
| [`enrich.py`](enrich.py) | The `enrich` command that runs a tool against an archive's Artifact |
| `support.py`, `test_*.py` | Synthetic audio, scratch archives built through Door, and the tests |

Everything uses only Python's standard library; the probe needs `ffmpeg` and
`ffprobe`. The tests also need World's test requirements, Door, and
`archive` (`make test-python`).

## `enrich probe`

```sh
enrich/enrich.py probe --archive <dir> --artifact <object id> [--staging <annex>] [--dry-run]
```

Counts every audio stream of the Artifact's current revision and proposes one
`archive.claim/audio_stream` claim per counted stream, all made by one
`archive.claim/derivation_run` claim, in one Door batch. It prints one JSON
object and exits 0 when the claims were committed (`committed`), predicted by
a dry run (`predicted`), or already recorded (`unchanged`); 2 when no stream
could be counted exactly (`nothing_proposed`, with each stream's reason); and
1 on any failure, with `{"status": "failed", "detail": ...}` on stderr.

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
  `refs` are the output's other pins, and must not include `run`.
- **`propose(reader, result, door, staging, dry_run, by, env)`** checks the
  proposer's obligations of D-15 (a Package URL agent, at least one output, a
  run that does not end before it starts, `used` pins to revisions in the
  archive) and builds one batch: the parameters Artifact when it is new, the
  run (`run-<uuidv4>` in `archive.derivation/run`), and every output. Each
  revision is computed in advance, so the outputs can pin the run, and Door's
  answer must equal them. It returns an `Admission` with the run, the
  outputs, the parameters (`reused` or not), and the revision identifiers.
- **Parameters** are written as RFC 8785 canonical JSON (`canonical()`). When
  an Artifact already holds exactly those bytes, the run pins its current
  revision. Otherwise the bytes are added to the `Staging` repository (a
  git-annex repository, created when absent), which is configured in the
  archive as a Git remote (an existing remote with the same path, or
  `enrich-staging`), and declared as content for Door to fetch and verify
  ([ADR 0023](../docs/decisions/0023-admission-and-staging.md)). Without
  `staging` a new parameters Artifact is refused.
- **A dry run** stages nothing and configures no remote; Door predicts the
  revisions without fetching anything.
- `AdapterError` carries every refusal, Door's included; a refused batch
  leaves the archive unchanged.

The adapter proposes only through `door admit`; `door serve` takes the same
proposal directory, so a long-running caller can switch without changing the
batch.
