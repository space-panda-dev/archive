# Capture

The native importer for new recordings
([ADR 0070](../docs/decisions/0070-capture-import.md)). It takes a directory
of recorder files, such as a card, and proposes them to Door as Artifacts
grouped into recording sessions, then runs the enrichment chain on what it
admitted. It never changes its input and never writes a record itself: it
reads the archive through `archive export` and proposes through `door admit`.

| File | What it is |
|---|---|
| [`capture.py`](capture.py) | The `capture import` command |
| [`send.py`](send.py) | The `capture send` command: a card read on one machine, imported on the archive's |
| [`grammar.py`](grammar.py) | Recorder grammars: what a recorder's file names say, and its rollover rule |
| `test_*.py` | Synthetic cards imported into scratch archives through the real Door and enrichment tools |

It uses the enrichment adapter ([`../enrich/adapter.py`](../enrich/README.md))
to read the archive, compute identities and revisions, and stage content, and
runs `enrich/enrich.py` and `enrich/media.py` for the chain. The importer
itself needs only Python's standard library; the chain needs what enrichment
needs (`ffmpeg`, `ffprobe`, numpy).

## `capture import`

```sh
capture/capture.py import --archive <dir> --staging <annex> --derived-media <root> <directory>
                          [--session-name <name>] [--jobs <n>] [--defer-listening-copies] [--dry-run]
```

It prints one JSON object with `status` `committed`, `predicted` (a dry
run), or `unchanged` (nothing new), the `sessions` it found, the `chain` it
ran, and the hidden names it passed over (`ignored`), and exits 0. On a
failure it prints `{"status": "failed", "detail", ...}` to stderr, with the
sessions and chain as far as they got, and exits 1; 2 means the arguments
were not understood. Every refusal of the input is decided before anything is
staged or proposed.

1. **Hash.** Every regular file under the directory is hashed. Hidden names
   (starting with `.`) are passed over and listed. A symbolic link, or
   anything else that is not a regular file or directory, refuses the run.
   So does a directory or file that cannot be read, rather than being left
   out, and a directory with nothing to import: an empty card is more likely
   a wrong mount than an empty recording.
2. **Group.** Each file name is offered to each grammar in turn. A file no
   grammar recognises refuses the run, unless `--session-name` is given:
   then every such file is one source of one session with that label, its
   parts in path order. A recognised session is labelled by its grammar and
   the recorder's clock (`Zoom H4essential 2001-01-01 12:00:00`); renaming it
   is a revision for a review client to propose.
3. **Skip what is known.** A session whose files' contents are all already
   current Artifacts (whoever admitted them) is `already_imported` and left
   alone. A session only partly known is refused: its parts claims would
   need new revisions, which this importer does not propose. The same
   content in two new files is refused, as is a new session whose session
   object already exists.
4. **Admit.** Each new session is one content-bearing `door admit` batch
   (ADR 0023): an Artifact per file, named `capture-<sha256>` in
   `archive.core/artifact`; the `recording_session` Entity, named
   `session-<sha256 of the primary source's first part>` in
   `archive.audio/session` (ADR 0048); and one `archive.claim/session_parts`
   claim per source, named `<session name>-<source>`, pinning the session
   and the source's parts in recording order. The records carry no time, so
   a dry run predicts exactly the revisions a real run commits. The bytes are
   copied into the staging repository under their digest, added with
   git-annex, and offered to the archive as the remote `capture-staging` (or
   any remote already configured with that path). The input files are only
   read. Writing program and agent are both
   `pkg:generic/archive-capture@0.1.0`: the grouping is the grammar's.
5. **Enrich**, quickest first, so a session can be reviewed before its
   slowest output exists. `enrich probe` on every new Artifact, then `enrich
   detect` with the `jam` preset on each part of each session's primary
   source: each run is its own batch, staged in the same staging repository,
   one at a time, and all of them read one `archive export` that is brought
   up to date with `archive export --since` after each commit, instead of
   exporting every record for each run. Then waveform peaks for every
   recording and the listening copies, each `--jobs` at a time with
   `enrich/media.py` (default: the processors less two).
   `--defer-listening-copies` leaves the listening copies for a later
   `enrich/media.py` run. Last, `enrich match-windows` on each detection,
   with `--jobs` processes, searches the whole recording and proposes the
   Works it finds, each in a window of whole suggested spans
   ([ADR 0072](../docs/decisions/0072-windowed-work-matches.md));
   it is the slowest step. The archive must install `work_match` version 3
   (specification 0.0.8): an older one is refused before anything is staged.
   A file whose probe
   counts no audio stream is reported with `nothing_proposed`, and nothing
   else runs on it. A failure stops the chain once the step it happened in
   has finished; the import does not resume it later, because its contents
   are then Artifacts and a rerun skips them (see the
   [runbook](../docs/shadow-runbook.md#importing-new-recordings)).

A dry run reads, hashes, groups, and asks Door to predict each batch; it
stages nothing, configures no remote, and runs no enrichment. The archive,
the staging repository, and the derived-media root must exist apart from the
input directory (the staging repository is created when absent), and the
derived-media root must already exist.

`--expect <manifest>` names a manifest of the files the directory must hold,
each with its digest and size, as `capture send` writes it. A directory
holding anything else, missing a file, or holding other bytes is refused
before anything is staged.

## `capture send`

A card is read on the machine it is inserted in; the archive is on its own
machine. [`send.py`](send.py) joins the two:

```sh
capture/send.py <card directory> --config <file> [--session-name <name>]
                [--jobs <n>] [--defer-listening-copies] [--dry-run]
```

1. It hashes every file on the card and groups it with the recorder
   grammars, so a card the importer would refuse is refused before anything
   crosses the network.
2. It streams exactly those files with `tar` over `ssh` into an inbox on the
   archive machine, in a directory named by the card's contents (a second
   send of the same card lands in the same place), and writes the manifest
   beside it.
3. It runs `capture import` there with `--expect` and the options given, and
   prints its answer and exits with its status.

The card is only read; nothing on it is moved or deleted. The configuration
is the operator's and stays outside this repository:

```json
{"host": "<ssh destination>",
 "inbox": "<absolute directory on the archive machine>",
 "import": ["<python>", "<.../capture/capture.py>", "import",
            "--archive", "<dir>", "--staging", "<annex>", "--derived-media", "<root>",
            "--door", "<door>", "--archive-command", "<archive>"]}
```

Put the inbox on the same file system as the staging repository: staging
then clones each file instead of copying it, and the network copy is the
only one written. The inbox keeps the copy after the import; removing it is
the operator's choice, once the archive's content is checked.

## The Zoom H4essential grammar

Names are `MMDDYY_HHMMSS_TrLR.WAV` (the built-in stereo pair) and
`MMDDYY_HHMMSS_TrMic.WAV` (the external input), both written by one take.
Exactly that form is recognised: another track token, a lowercase extension,
a numbered suffix, or an impossible date is not this grammar. `TrLR` is the
first source and so the primary one; `TrMic` is second.

**Rollover.** The recorder stops a file at exactly 2,147,075,072 bytes and
starts a new take at once. A take continues the previous take of its session
when the previous file is exactly that size and the next take starts within
two seconds (inclusive) of the previous file's end, which is its start plus
the duration its WAV header states (the `data` chunk's size over the `fmt `
byte rate, compared exactly). Both conditions are needed, as the V2 system
measured: the gap alone joins two short takes made seconds apart; the size
alone joins a take that stopped at the cap with nothing after it. Every
source of a take must agree: a rollover splits every track at once, so one
source continuing and another not, or a continuing take missing a source, is
refused rather than resolved. A capped file whose header states no duration
is refused too.

## Test

```sh
python -m unittest discover -s capture -p 'test_*.py'
```

The cards are synthetic: numpy chords and noise in WAV files with invented
names. Rollovers are tested end to end with a grammar whose cap is the size
of the synthetic file, and the real cap with sparse files that occupy almost
no disk. The end-to-end tests need Door, `archive`, ffmpeg, and the
enrichment requirements, and are skipped without them unless
`ARCHIVE_REQUIRE_INTEGRATION` is set (`make test-python`).
