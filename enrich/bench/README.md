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
| [`region.py`](region.py) | The region check: `extract.py region-equivalence` over the same clips (ADR 0112) |
| [`chords.py`](chords.py) | The chord bench: chord sets, references from a chart and tapped section downbeats, mir_eval chord, key, meter, downbeat and section scores, the `bank-proposers` variant, and its gate (ADR 0116, proposed) |
| [`__main__.py`](__main__.py) | The command |
| [`test_bench.py`](test_bench.py), [`test_chords.py`](test_chords.py) | The harness and the chord bench on tiny synthetic sets and invented charts |

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

`$P -m enrich.bench region` checks that a region's entries stand for the
whole run's under `--songs` (ADR 0112): over the same clips, it makes the
stems and what measures them (notes-basic-pitch and f0-pesto of the vocals
and bass, melody of the vocals) whole and over a region (15 to 45 s), and
compares them over the region with the bounds `stems` declares for a
stand-in (note F1 at least 0.98, f0 p99 at most 5 cents, voicing at least
0.99; melody held to the f0 bounds). Each stem's sample difference is
reported. Settings: the `equivalence` object, and an optional `region`
object (`bank`, `seconds`, `programs`; see [`region.py`](region.py)). Run it
under an interpreter with Essentia for melody, or name one in
`region.programs.python`. Exit 0 every region agrees, 1 one does not, 2 it
could not be made.

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
Each unit carries its backfill tier, and the result carries the two
headline numbers of ADR 0112 per audio-second on this machine alone:
`time_to_usable_per_audio_s` (the fast tier's CPU seconds times the
backfill's load factor, over the cores) and `time_to_done_per_audio_s`
(every unit's). Time-to-usable is gated on its own: the run fails when the
fast tier's sum rises by more than 20%, so a fast unit that grows cannot
hide behind `stems`. A baseline recorded before tiers is compared by the
result's tiers.
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

## The chord bench

[ADR 0116](../../docs/decisions/0116-ireal-charts.md) section 6 (proposed):
a chart placed on a recording is a reference no extractor made, so it
measures the machine's chords, key, meter, downbeats and sections. See
[`chords.py`](chords.py) for every rule. Under the same bench root:

- `sets/<name>.json`, contract `archive-bench-chord-set/1`, task `chords`:
  per item `id` (the recording's sha256), `label`, `subset`, and files
  `recording` (with `seconds`), `chart` (the chart document) and
  `reference`.
- A reference (`archive-bench-chord-reference/1`) holds the recording's
  and the chart's digests, `placement` (`person` or `chord-blind`; a
  reference is never made from the chords it scores), `transposition`,
  `stretch` (the placed seconds), `chords` (`[start, end, Harte]`, meeting,
  from World's computed names), `key` (a mir_eval key or null), `numerator`,
  `downbeats` and `sections`.

```
$P -m enrich.bench chords reference --claims chart.json --taps taps.txt \
   --recording take.flac --chart chart.irealb [--transposition N] --out ref.json
$P -m enrich.bench chords validate SET
$P -m enrich.bench chords run SET --variant bank-proposers [--record-baseline]
$P -m enrich.bench chords check SET
```

`reference` takes the chart's untimed payloads as JSON (`meter`, `form`,
`sequence`, optional `key`: what the iReal importer proposes) and a person's
taps: one time per played section's downbeat, in World's played order from
`--first-occurrence`, then the end of the last; bars between two taps share
the time by their beats. No chord-blind run exists yet.

The `bank-proposers` variant reads a feature bank already filled for each
recording (`config.json`: `variants.bank-proposers.bank`, optional `chords`
and `config`) through `enrich propose`'s proposers, so it scores what the
archive would be told; it runs no extractor. Scores are over the placed
stretch only; a metric the machine gave nothing for is absent, not zero, and
a mean is over the items that have it. The gate: any mean, over all items
and per subset, dropping by more than 0.02 fails (exit 1); a metric the
baseline has and the result lacks stops the comparison (exit 2).
