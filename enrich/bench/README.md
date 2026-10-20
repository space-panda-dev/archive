# The bench: regression sets for note transcription, and the backfill's cost

The bench scores pipeline variants against fixed evaluation sets and fails
when a change makes them worse. It is the first piece of the experiment floor
in the [backlog](../../docs/backlog.md#lead-sheets-and-practice); its first set, `horn-v1`, holds
separated single-line horn solos. The bench proposes nothing and never reads
or writes an archive.

| File | What it is |
|---|---|
| [`sets.py`](sets.py) | A set's manifest, its validation, and the check that every file it names is present and unchanged |
| [`score.py`](score.py) | mir_eval note precision, recall and F1, without and with offsets; reading notes from MIDI |
| [`variants.py`](variants.py) | The variant interface and the registry: `basic-pitch` (the bank's `notes-basic-pitch` extractor) and `horn-d0` (an adapter, see below) |
| [`run.py`](run.py) | Running a variant over a set, the result document, baselines, and the gate |
| [`cost.py`](cost.py) | The backfill's cost gate: CPU seconds per audio-second of every backfill unit over one clip |
| [`equivalence.py`](equivalence.py) | The stem equivalence check: `extract.py equivalence` over the root's clips (ADR 0090's amendment) |
| [`__main__.py`](__main__.py) | The command |
| [`test_bench.py`](test_bench.py) | The harness on tiny synthetic sets |

## Running it

The data is private and lives outside the repository, on the x86-64 laptop,
under a bench root named by `ARCHIVE_BENCH_ROOT` (or `--root`). The harness
runs under an interpreter with numpy, mir_eval and pretty_midi (the root's
`envs/harness`); each variant runs under its own interpreter, which the root's
`config.json` names. From the repository root:

```
export ARCHIVE_BENCH_ROOT=~/archive-bench
P=$ARCHIVE_BENCH_ROOT/envs/harness/bin/python
$P -m enrich.bench validate horn-v1                    # manifest and every file's digest
$P -m enrich.bench run horn-v1 --variant horn-d0       # score, record, compare with the baseline
$P -m enrich.bench run horn-v1 --variant basic-pitch --input mix
$P -m enrich.bench run horn-v1 --variant horn-d0 --record-baseline
$P -m enrich.bench check horn-v1                       # rerun every baseline; exit 1 on a regression
```

`wf check bench` runs the unit tests and `wf check bench-horn` runs `check
horn-v1` on the laptop.

`$P -m enrich.bench equivalence` checks that the Mac GPU's stems stand in for
the CPU's: it runs `enrich/extract.py equivalence` over the clips and bank the
root's `config.json` names under `equivalence` (see
[`equivalence.py`](equivalence.py)), makes the CPU side where it is missing,
and files the record in that bank. The GPU side must be there already, made
by the Mac's worker through Spool and collected. Exit 0 the record passes, 1
it fails, 2 it could not be made. Declared in wf's suites, it is
`bench-stems`. The suite is cached by source tree, so after changing
the data or a variant's environment ask again with `--fresh`.

Exit status: 0 passed, 1 a mean F1 dropped by more than the tolerance (0.02
by default, over all items and per subset, with and without offsets), 2 the
run could not happen. A missing or changed file, a variant that fails, or one
that writes no notes stops the run with status 2; it never scores as zero.

## The backfill's cost

`$P -m enrich.bench cost` runs every unit of `enrich backfill` (its
configuration's units, in `unit_order`) once over the root's clip, each as
the backfill runs it: `extract.py run` in a process of its own, under the
enrich interpreter, held to the threads `backfill.layout` gives it, into an
empty bank. It records each unit's CPU seconds (the process and its
children) per audio-second and their sum, files the result under
`results/cost/`, and compares it with `baselines/cost/backfill.json`. It
fails (exit 1) when the sum rises by more than 20%, or one unit more than
doubles (by more than 100% plus 0.02 s per audio-second: a cheap unit moves
by 2x between runs on the same machine, so only the sum is gated finely); `--record-baseline` stores the run
as the baseline. CPU time is gated, not wall time, because other load on
the machine moves the wall far more. A baseline of another clip, machine
architecture or core count, or other units, cannot be compared (exit 2).
The root's `config.json` names it:

```json
"cost": {"python": "<the enrich interpreter>", "config": "cost/extract-config.json", "clip": "cost/clip.wav"}
```

The clip is two minutes of a song with voice and bass, so every unit has
work; the configuration is the backfill's (its programs). One run takes
about four minutes on the x86-64 laptop.

## Scoring and cost

Notes are scored by `mir_eval.transcription.precision_recall_f1_overlap` at
the set's tolerances (for `horn-v1`: onsets within 50 ms, pitch within 50
cents; with offsets, within 20% of the reference note's length or 50 ms). A
result records each item's precision, recall and both F1s, the means, the
variant's name, version, parameters and provenance (software versions, digests
of the programs it ran), and the set manifest's digest. Cost is wall-clock
seconds per minute of audio, model loading included; it is recorded and
printed beside the baseline's, not gated, since the machine's load moves it.

## Where the data lives

```
$ARCHIVE_BENCH_ROOT/
  sets/<set>.json          the manifest (contract archive-bench-set/1)
  config.json              each variant's interpreter and programs (archive-bench-config/1)
  data/                    audio, separations, references, and the sources they were cut from
  envs/                    the harness's and the variants' interpreters
  adapters/                private programs a variant runs until it is ported
  results/<set>/<variant>/ every run's result
  baselines/<set>/<variant>@<input>.json   the result a run is compared with
```

None of it is committed, and nothing here names a recording. A manifest lists
each item by the sha256 of its primary input, with its label, subset, its
inputs (each a path under the root, a digest and a length in seconds), its
reference (path and digest), and, for a person rather than the harness, how
it was made: the source and the span cut from it, the alignment of the
reference (scale and offset), the separation, and the licences.

`horn-v1` has two subsets. `solo-db`: three solos of one track, scored on the
htdemucs_6s "other" stem against a jazz-solo database whose time base was
fitted to the recording (a track-wide scale and offset, then a linear
correction per solo). `omnibook`: ten tunes of an aligned saxophone omnibook,
scored on its provided saxophone stem against its performance-aligned MIDI,
restricted to tunes whose tuning correction is at most 0.1 semitone. The
other inputs kept per item (the htdemucs "other" stem and the mix) can be run
with `--input`.

## Adding a set or a variant

- **A set.** Put its files under `data/`, write `sets/<name>.json` by a script
  that digests the real files (never by hand), split by song or session so
  nothing used for tuning is scored, run `validate`, then record each
  variant's baseline with `--record-baseline`.
- **A variant.** A class with `name`, `version`, `parameters()`,
  `provenance()` and `transcribe(audio, work)` returning `(onset_s,
  offset_s, midi)` notes, listed in `VARIANTS` in `variants.py`, with its
  settings in `config.json` and a case in the registry test. Record its
  baseline once; from then on `check` gates it.
- **`horn-d0` is an adapter.** Until the PESTO note builder is in the feature
  bank, `horn-d0` runs the transcription spike's private scripts, named by
  `config.json`. When the bank has it, a variant calling the bank's
  extractors replaces the adapter under the same name and its baseline is
  recorded again.
