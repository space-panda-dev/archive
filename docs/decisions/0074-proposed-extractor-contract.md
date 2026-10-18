# 0074. Proposed: an extractor is any program with a manifest, run by request

Status: proposed

The feature bank ([ADR 0073](0073-proposed-feature-bank.md)) needs writers,
and the operator wants adding one to be easy and independent of language:
Essentia's algorithms are Python, a stem separator runs in its own
environment with PyTorch, and a measure may be a shell script around
`ffmpeg`. ADR 0014 already says enrichment tools "may be written in anything
that accepts their declared input and produces their declared output"; this
record says what is declared and how a program is run. Work will later be
handed to other machines through Spool, which carries opaque JSON payloads,
content-addressed attachments, and capabilities named `name@version`, so a
run must be describable without shared memory or shared paths.

## Proposed decision

**An extractor is an executable with a manifest.** Each lives in its own
directory with an `extractor.json` (contract `archive-extractor/1`) that
declares:

- `id` and `version`: the extractor's name and the version of its method;
  any change to its outputs' meaning or bits is a new version;
- `command`: the argument list that runs it. A word `${name}` is a program
  the operator configures (`python`, `sh`, or `demucs-python`), as ADR 0033
  makes Git's path configuration; a word naming a file in the extractor's
  directory becomes that file's path;
- `accepts`: the media types of content it measures, such as `audio/*`;
- `needs`: the features it reads, as `<extractor>/<output>`;
- `parameters`: its settings, as JSON whose numbers are integers (a fraction
  is decimal text, as ADR 0064 requires of a run's parameters);
- `outputs`: each output's name and kind, and for a track its element type,
  columns, and grid, for a file its media type and suffix, and a unit;
- `requires`: the capabilities a machine must offer to run it (a library
  build, a model), which Spool would match.

**It is run with one request and writes one directory.** The runner invokes
`command` with the path of a request JSON (contract
`archive-extractor-request/1`) holding the extractor's id and version, its
key, its parameters, the input's path, digest, and media type, every needed
feature with its path, shape, and grid, and the output directory. For
programs without a JSON parser it also exports the request's path, the
input's path, and the output directory as `ARCHIVE_EXTRACTOR_REQUEST`,
`ARCHIVE_EXTRACTOR_INPUT`, and `ARCHIVE_EXTRACTOR_OUTPUT`; the request is
authoritative. The extractor writes each output's file and a `result.json`
(contract `archive-extractor-result/1`) giving each track's shape, each
scalar's value, its grids, and the software it used, and exits 0. Anything
else is a failure; what it prints is kept only as a failure's log.

**The runner checks before it files.** The outputs must be exactly the
declared ones, no other file may be present, a track's bytes must match its
shape and hold only finite values when it is floating point, and events and
segments must be well-formed. Only then does the runner write `entry.json`
and rename the directory into the bank. Each kind of output is checked by the
runner itself, so a new kind is added with its check.

**One runner, standard library only.** `enrich/extract.py run <extractor|all>`
orders the named extractors and everything they need, skips what is already
in the bank without running or hashing anything, runs what is missing, up to
`--jobs` at a time, and reports each as `hit`, `ran`, `failed`, `blocked`, or
`skipped`. A failure blocks only what needs it. `--dry-run` prints each
missing run as a task: its capability `archive-extractor/<id>@<version>`, its
requirements, its parameters, its input's digest, and the keys it must wait
for. The ordering is a few extractors per content; distributing work stays
Spool's ([boundaries](../boundaries.md)).

**A run is portable by construction.** Every input a request names has a
digest, and every output is a file the runner checks after the fact, so a
Spool worker could receive the request as a payload and the content and
needed files as attachments named by SHA-256, run the same command, and
return the directory for the requester's runner to check and file. Nothing
is wired to Spool, and how a directory of binary outputs returns through it,
whose results are JSON, is open
([open questions](../open-questions.md#interfaces-and-tooling)).

**Other media use the same contract.** An extractor for photographs accepts
`image/*` and writes scalars, segments over its own units, or files; one for
video accepts `video/*` and may read audio features of the same content. A
grid is a time base in integer samples: an integer `sample_rate`, a `hop`,
an optional `frame` length, and the sample `first_centre` on which row 0 is
centred. Version 1 cannot express spatial grids or video's fractional frame
rates; both are left open.

**Two extractors ship.** `frames` (Python with Essentia) decodes once and
writes the base tracks every audio measure can reuse, on one grid: the
pitch-class profile exactly as the window matcher computes it, frame level,
spectral centroid, roll-off, flatness, and flux, an onset function, MFCCs,
and mel bands. `loudness-r128` is a 31-line shell script around `ffmpeg`
that writes one scalar, the worked example of
[adding an extractor](../adding-an-extractor.md), and the proof that the
runner does not care what an extractor is written in.

### Why a new noun

*Extractor* passes the [new-noun test](../vocabulary.md#adding-a-noun):

1. It is the only thing that writes the feature bank, under a contract any
   language can meet.
2. Its verb is *extract*: an extractor extracts features from content.
3. It enforces that every feature is declared before it is written and
   checked before it is filed.
4. It is a kind of enrichment tool, not a new concept; the noun names the
   contract, not a subsystem.
5. It owns no authoritative state.
6. It is a protocol between the runner and a program.

## Consequences

- Adding an audio measure is a directory with a manifest and a program; no
  file in the runner changes unless a new output kind is needed.
- A stem separator runs in its own environment, named by configuration, and
  its stems are `file` outputs that later extractors need like any feature.
- The plugin and dependency-injection frameworks
  [left out on purpose](../boundaries.md#left-out-on-purpose) stay out: an
  extractor is a separate process with declared input and output, as
  ADR 0014 already allows, and nothing is loaded into the runner.

## Alternatives considered

- **Python functions registered in the runner.** Every measure would need
  the runner's interpreter and libraries, and a stem separator's PyTorch
  environment could not be one of them.
- **Arguments instead of a request file.** Needed features, shapes, and
  grids do not fit a command line, and a request file is what a Spool
  payload would carry anyway.
- **Extractors writing into the bank themselves.** Every one would have to
  get the layout, the keys, and the atomic rename right; the runner does it
  once and checks the outputs first.
- **Standard output as the result.** It mixes results with log lines, and
  binary tracks and stems do not belong on a pipe.
