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
  any change that can alter its outputs' meaning or bits is a new version
  (ADR 0073 says how a test holds shipped code to it). A version is letters,
  digits, and dots, at most 40 characters, so that
  `archive-extractor.<id>@<version>` is a Spool capability (below);
- `command`: the argument list that runs it. A word `${name}` is a program
  the operator configures (`python`, `sh`, or `demucs-python`), as ADR 0033
  makes Git's path configuration; a word naming a file in the extractor's
  directory becomes that file's path;
- `accepts`: the media types of content it measures, such as `audio/*`;
- `needs`: the features it reads, as `<extractor>/<output>`;
- `optional`: features it reads when it can and does without otherwise,
  such as a vocal stem a melody is better measured on, or a measured tuning
  reference. One is a need when the catalog has an extractor with its id,
  and is dropped when it has none; whether it is read is therefore decided
  by the installed extractors, never by what a run happened to find, and it
  is in the key exactly when it is read. When the extractor is installed and
  fails, or does not accept the content, what reads it is blocked or
  skipped as for any need, rather than measured without it under the same
  key. A name whose extractor is installed but declares no such output is
  refused, so a misspelling is not silently dropped; one whose extractor is
  not installed cannot be checked, and `extract.py list` shows what each
  extractor reads from the catalog as it stands;
- `parameters`: its settings, as JSON whose numbers are integers (a fraction
  is decimal text, as ADR 0064 requires of a run's parameters);
- `outputs`: each output's name and kind, and for a series its element type,
  columns, and grid, for a file its media type and suffix, and a unit;
- `requires`: what a machine must have installed to run it (a library
  build, a model), as text for the operator. Spool matches nothing but the
  capability name, so these become the configuration of the worker that
  offers the capability, not something Spool checks.

**It is run with one request and writes one directory.** The runner invokes
`command` with the path of a request JSON (contract
`archive-extractor-request/1`) holding the extractor's id and version, its
key, its parameters, the input's path, digest, and media type, every needed
feature with its path, shape, and grid, and the output directory. For
programs without a JSON parser it also exports the request's path, the
input's path, and the output directory as `ARCHIVE_EXTRACTOR_REQUEST`,
`ARCHIVE_EXTRACTOR_INPUT`, and `ARCHIVE_EXTRACTOR_OUTPUT`; the request is
authoritative. The program runs in a process group of its own: when it
exits, whatever is left of the group is killed, and when it runs past the
timeout the group is sent SIGTERM and, five seconds later, SIGKILL, as a
Spool worker stops a run. A shell script around `ffmpeg` or a separator
therefore never leaves a child running after the runner has given up on it,
or writing into a directory the runner is checking. The extractor writes each output's file and a `result.json`
(contract `archive-extractor-result/1`) giving each series' shape, each
scalar's value, its grids, and the software it used, and exits 0. Anything
else is a failure; what it prints is kept only as a failure's log.

**The runner checks before it files.** The outputs must be exactly the
declared ones, no other file may be present, every file must be a regular
file with one link (a hard link to the input would share the archive's
content inode, and making the entry owner-only would change the content's
mode, against INV-9), a series' bytes must match its
shape and hold only finite values when it is floating point, and events and
spans must be well-formed. Only then does the runner write `entry.json`
and rename the directory into the bank. Each kind of output is checked by the
runner itself, so a new kind is added with its check.

**One runner, standard library only.** `enrich/extract.py run <extractor|all>`
orders the named extractors and everything they need, skips what is already
in the bank without running or hashing anything, runs what is missing, up to
`--jobs` at a time, and reports each as `hit`, `ran`, `failed`, `blocked`, or
`skipped`. A failure blocks only what needs it, and so does a damaged entry,
which is reported with the advice to delete it. Two runners asked for the
same entry make it once (ADR 0073). Configured parameters must name an
extractor and fields its manifest declares; a misspelt name is refused
rather than ignored. `--dry-run` prints each missing run as a task: its
capability `archive-extractor.<id>@<version>`, its requirements, its
parameters, its input's digest, and the keys it must wait for.

**The runner is not a workflow engine or a scheduler.** Those are left out
on purpose ([boundaries](../boundaries.md#left-out-on-purpose)), and the
runner stays inside the line: its graph is the handful of extractors one
content needs, ordered from their manifests on each run; it runs them on
one machine, keeps no state between runs but the bank itself, retries
nothing, and dispatches nothing elsewhere. Waiting for work, retrying it,
and sending it to other machines stay Spool's.

**A run is meant to travel, through a shim.** Every input a request names
has a digest, and every output is a file the runner checks after the fact,
so a run can be described without shared memory. Spool cannot carry it as it
stands, though. A Spool capability is `name@version` with a name of letters,
digits, `.`, `_`, and `-`, a version of letters, digits, and dots, and 128
characters in all, which `archive-extractor.<id>@<version>` meets for every
id and version the manifest grammar allows (a test checks the extremes).
But a Spool program gets its payload on standard input in a fresh directory,
with an environment its worker configures, and returns JSON on standard
output, capped in size; an extractor gets a request path as an argument and
`ARCHIVE_EXTRACTOR_*` variables, and returns binary files in a directory. A
worker-side shim is therefore needed: it would write the payload to
`request.json`, place the attachments where the request's paths say, run
the command, and return the directory by some path that Spool's JSON result
does not provide. Nothing is wired to Spool, and that return path is open
([open questions](../open-questions.md#interfaces-and-tooling)).

**Other media use the same contract.** An extractor for photographs accepts
`image/*` and writes scalars, spans over its own units, or files; one for
video accepts `video/*` and may read audio features of the same content. A
grid is a time base in integer samples: an integer `sample_rate`, a `hop`,
an optional `frame` length, and the sample `first_centre` on which row 0 is
centred. Video's fractional frame rates fit it (`sample_rate` 30000 and `hop`
1001 is 29.97 frames a second). Version 1 cannot express spatial grids, and
spans are one-dimensional, so a region of an image has no kind yet; both are
left open. Times count from the first sample of the stream the extractor
decoded, and a stream's start offset in its container is not recorded, so a
video extractor relating its frames to `frames` series adds the audio
stream's start time itself.

**Extractors ship.** `frames` (Python with Essentia) decodes once and
writes the base series every audio measure can reuse, on one grid: the
pitch-class profile exactly as the window matcher computes it, frame level,
spectral centroid, roll-off, flatness, and flux, an onset function, MFCCs,
and mel bands. `loudness-r128` is a 31-line shell script around `ffmpeg`
that writes one scalar, the worked example of
[adding an extractor](../adding-an-extractor.md), and the proof that the
runner does not care what an extractor is written in. `melody` (Python with
Essentia) measures the melody's pitch contour, voicing, and intonation with
MELODIA, on a vocal stem and against a measured tuning reference when the
catalog has extractors for them: the first user of optional needs.

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
- "Decode once" holds for measures on the `frames` grid. An extractor on
  another grid or rate (beats, key, momentary loudness, a separator at
  44.1 kHz) decodes the content again, as `loudness-r128` does; the bank
  has no decoded-audio feature, since 4 hours of mono 22,050 Hz audio is
  about 1.3 GB as 32-bit floats, larger than every series together.
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
  binary series and stems do not belong on a pipe.
