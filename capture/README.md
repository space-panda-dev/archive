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
                          [--session-name <name>] [--dry-run]
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
   anything else that is not a regular file or directory, refuses the run,
   and so does a directory with nothing to import: an empty card is more
   likely a wrong mount than an empty recording.
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
5. **Enrich.** After every new session is committed, for each new Artifact
   in session order: `enrich probe`; then, for each part of the primary
   source, `enrich detect` with the `jam` preset; then `enrich/media.py` into
   the derived-media root. Each enrichment run is its own batch, staged in
   the same staging repository. A file whose probe counts no audio stream is
   reported with `nothing_proposed`, and nothing else runs on it. The first
   failure stops the chain; the import does not resume it later, because
   its contents are then Artifacts and a rerun skips them (see the
   [runbook](../docs/shadow-runbook.md#importing-new-recordings)).

A dry run reads, hashes, groups, and asks Door to predict each batch; it
stages nothing, configures no remote, and runs no enrichment. The archive,
the staging repository, and the derived-media root must exist apart from the
input directory (the staging repository is created when absent), and the
derived-media root must already exist.

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
