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
runner does not care what an extractor is written in. Seven more measure
from Essentia's standard algorithms or from what is already in the bank:
`rhythm` (beats, tempo, onsets, how far onsets sit off the beat, and the
tempo level, meter, and downbeats a musician would count),
`tonal` (tuning and its drift, key, chords, dissonance), `melody` (the
mix's predominant melody with MELODIA), `structure` (playing, tuning,
count-ins, false starts, endings, sections, and loops, from `frames` and
`rhythm` without decoding), `quality` (how well the recording was captured),
`colour` (a colour-coded waveform's data), and `stems` (Demucs, in an
environment of its own; [ADR 0075](0075-proposed-stems-as-content.md)).
What each writes is listed in one place, the table in
[`enrich/README.md`](../../enrich/README.md#extractpy-the-feature-bank);
the manifests are authoritative.

**A need is fixed; there is no optional one.** An extractor reads exactly
the features its manifest needs, whatever else is installed. A need that
became one only when the catalog held its extractor was tried for the
melody (a vocal stem, a measured tuning) and dropped: an extractor in the
catalog is not one that can run here (a stem separator whose program is not
configured would block the melody on every such machine, and where it can
run, every melody would first pay for a separation), and an output would
change what it means under the same name when something unrelated was
installed (the vocal stem's melody is silent on an instrumental recording,
where the mix's would follow the lead instrument). Measuring a stem is
running an extractor on the stem as its own content, and a measured tuning
is applied by whoever reads both the melody and the tuning.

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
- "Decode once" holds for measures on the `frames` grid that need only
  what `frames` keeps. An extractor on another grid or rate (beats,
  momentary loudness, a separator at 44.1 kHz) decodes the content again,
  as `loudness-r128` does; the bank has no decoded-audio feature, since
  4 hours of mono 22,050 Hz audio is about 1.3 GB as 32-bit floats, larger
  than every series together.
- So does an extractor on the `frames` grid that needs a per-frame
  intermediate `frames` does not keep. `tonal` is the first: its tuning,
  pitch gate, and dissonance need each frame's spectral peaks, a ragged list
  that no output kind declares, so it decodes with `frames`' own decoder and
  framing, repeats its windowing, spectrum, and peak picking, and checks
  that its rows are `frames`' rows. That about doubles a recording's
  spectral cost: on a 600 s synthetic recording, `tonal` took 0.58 s of
  processor time beside `frames`' 0.73 s (both without ffmpeg's decode),
  or about 3.5 s against 4.4 s a recording-hour. Removing it would take a new `frames` version (a new key for every
  `frames` entry) with new outputs, such as each frame's 100-bin deviation
  of peaks from the tempered grid (which would serve the tuning and the
  pitch gate, at 400 bytes a row, about 15 MB a recording-hour, more than
  all of `frames` today) and its dissonance. At that cost it is not worth
  it for one reader; it becomes worth it when a second extractor needs the
  same peaks. An extractor that repeats `frames`' work says so in its
  manifest's description, so that the cost is visible where it is chosen.
- `quality` is the measured case of a second decode at another rate: it
  decodes again at the stream's own rate
  with every channel, because downmixing and resampling to 22,050 Hz remove
  what it measures (one channel's defects, the stereo image, everything
  above 11,025 Hz, single-sample steps and flat tops), and it reads only
  `frames/samples` from the bank, so its window series keep the `frames`
  rows. On 48 kHz stereo its second decode runs at about 16 times real time
  on one core of an otherwise idle machine and about 9 times on a shared
  one (load average near 13), where `frames` ran at about 150: 15 to 30
  core-minutes for a four-hour recording, the detectors' frame loop and
  not the decode being the cost, so it grows with the sample rate.
- What each shipped extractor decodes: `frames` the first audio stream
  at 22,050 Hz mono, once; `structure` nothing; `tonal` the same again
  (above); `rhythm` and `melody` 44,100 Hz mono, which their algorithms
  are tuned for; `quality` the stream's own rate and channels (above);
  `colour` enough to learn the peaks' grid (ADR 0073); `loudness-r128`
  and `stems` through `ffmpeg` at the rate they need. Run together by
  `run all` on a synthesised three-minute 48 kHz stereo FLAC, on a shared
  development Mac (load average 13 to 15), one at a time, each took, in
  times real time: `structure` about 600, `loudness-r128` 450, `colour`
  140, `frames` 83, `tonal` 78, `rhythm` 36, `melody` 16, and `quality`
  9; about 43 seconds in all, or roughly 14 minutes a recording-hour, of
  which `quality` and `melody` are three quarters
  ([`enrich/test_extractors.py`](../../enrich/test_extractors.py) measures
  it). That `rhythm` was version 1. Version 3, which adds tempo levels,
  meter, and downbeats from spectral rows of its own, was timed later on
  the same recording against version 1, alternately and twice each, on the
  same Mac at a load average near 9: 37 to 38 times real time against 42
  to 44, about an eighth slower, the cost of the rows' Fourier transforms
  and two per-second support tables (the analysis after the rows took 5.6
  CPU-seconds a recording-hour on a 70-minute album). `stems` there ran a
  stand-in for the model, so its time says nothing about Demucs. Real recordings have not been timed together.
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
