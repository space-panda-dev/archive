# 0076. An extractor may prefer a file feature of its content as its default input

Status: accepted (operator, 2026-10-19); proposed 2026-10-18, amended before
acceptance (a mostly unvoiced preferred stem falls back to the mix; bass is
its own category); a second amendment of 2026-10-19, a probe before the
measure, is proposed for the operator to confirm

The melody of a band recording is the sung line, and the sung line is in the
vocal stem. Measured on the mix, MELODIA follows whichever line is most
salient, as often a keyboard, a bass, or a horn as the voice: on an album of
eighteen jazz and neo-soul tracks, the mix's melody agreed with the vocal
stem's on a median of about half of the rows the stem voiced. ADR 0075 made a
stem measurable as content (`--from stems/vocals`), but nothing made the
stem's melody *the* melody of a recording: every run had to ask for it, and
every reader had to know to look for it. The album test asked for the vocal
stem to be the default input wherever one exists.

[ADR 0074](0074-extractor-contract.md) already rejected the obvious way, a
need that is optional: an extractor in the catalog is not one that can run
here, so the melody would block on every machine without a separator; where
one runs, every melody would first pay for a separation; and an output would
change what it means under one name when something unrelated was installed.

## Proposed decision

**A manifest may list `prefers`: file features of its content that the
extractor measures by default in place of the content, in order, each with
parameters it takes there.** `melody` declares
`"prefers": [{"input": "stems/vocals", "parameters": {"bin_cents": 2}}]`.

**The default input is the first preferred feature the bank already holds
for the content, else the content.** Without `--from`, the runner runs every
other target first, then each preferring target on its default input, as
`--from` would: the feature's digest is the content, and the entry is filed
under the content's directory (ADR 0075). A preference never makes the
feature, and an extractor run only because another needs it measures the
content, since needs are of the content. `--no-prefer` measures the content.
A reader asks `Bank.default_entry(content, extractor)`, which decides by the
same function the runner does (`Bank.default_input`) and returns the entry
with the feature it measured.

**A preference's parameters apply whenever the extractor measures that
feature,** by preference or by `--from`, before the operator's configured
fields, which replace them in turn. They are part of the entry's parameters
and so of its key. A reader of a measure of a feature names the feature
(`Bank.entry_from`); a lookup under another content's directory of an
extractor that prefers a feature with parameters is refused without it,
rather than answered with nothing.

**A preferred feature is a `file` output of an extractor in the catalog, not
one the extractor makes or needs.** A preference naming an extractor that is
not in the catalog is never present.

This meets ADR 0074's objections. Nothing waits for a feature that cannot be
made here, and nothing pays for one that is not already made. No entry
changes its meaning: each is keyed by the bits it measured and says which in
its `content` and `parameters`. What changes when a stem is made is which
entry is a recording's default, and the reader is told which.

## Consequences

- [The feature bank format](../feature-bank.md#preferred-inputs) gains
  `prefers`; the key's parameters gain the preference's. `enrich/bank.py`
  checks the field and gains `overrides_for`, `entry_from`, `default_input`,
  and `default_entry`; `extract.py run` gains the default inputs and
  `--no-prefer`. A manifest without `prefers` behaves exactly as before.
- `melody` 3 is the first to prefer: the vocal stem in 2-cent bins, the mix
  in 5-cent bins, which cost about as much as Essentia's default of 10.
- On an instrumental recording the vocal stem holds bleed, and its melody is
  nearly empty (three instrumental tracks of the album voiced 3 to 20 % of
  their rows on the stem) where the mix's would follow the lead instrument.
  As proposed, the default stayed the stem's, and whether it should fall
  back to the content was left open; the
  [amendment](#amendment-2026-10-19-a-mostly-unvoiced-stem-falls-back-to-the-mix)
  settles it: it falls back.
- A dry run decides from the bank as it stands, so a `run all --dry-run` that
  would make the stems reports the melody on the mix, and the run itself
  measures the stem it has just made.
- A Spool worker's task for a preferred measure is the `--from` task: the
  runner, not the worker, decides the input.

## Evidence

`melody` 2 and 3 on that album (eighteen tracks, 4,166 s), on an eight-core
x86-64 laptop at low priority with one BLAS thread, each track measured by
both versions in turn so that both saw the same load. Version 3 measures the
mix in 5-cent bins and, by default, the vocal stem in 2-cent bins within a
second of the gate:

| | `melody` 2 | `melody` 3 |
|---|---|---|
| CPU seconds, mixes | 360 (11.6 times real time) | 268 (15.6 times) |
| CPU seconds, vocal stems | 366 (11.4 times) | 271 (15.4 times) |
| rows measured on the stem (median) | all | 82 % |
| mix agrees with the stem within 50 cents, of the rows the stem voices (median) | 55 % (60 % allowing octaves) | 58 % (62 %) |
| stem: rows voiced alike, 2 against 3 (median) | | 95 %; of the rows both voice, 98 % within 5 cents |

The mix's agreement with the stem was the same in 5-cent bins as in 2 (55 %).
The rows the stem's melody no longer voices were all within the gate's
reach, about 10 dB quieter than the rows both versions voice, at a third of
their confidence, and the mix agreed with them on 0 to 22 % against 14 to
85 %: MELODIA's voicing is relative to the contours in the audio it is given,
and without the stem's long stretches of bleed it voices less of what is
left.

## Alternatives considered

- **An optional need.** Rejected by ADR 0074, for the reasons above.
- **An extractor per stem** (`vocal-melody`). Rejected by ADR 0075: each
  measure would be written twice.
- **A convention in the reader's code** naming the melody's stem. It holds for
  one language only, and the runner and reader could disagree; the manifest
  is what every implementation of the format reads.
- **Choosing the bins by the operator's configuration per run.** A reader
  could not tell which parameters keyed the stem's entry; the preference
  carries them, so every reader computes the same key.

## Amendment, 2026-10-19: a mostly unvoiced stem falls back to the mix

Decided by the operator on accepting this record. The album rerun of
2026-10-18 measured what the proposal predicted: on the album's three
instrumentals the vocal stem's melody voiced 17, 7, and 3 % of its rows
(`voiced_fraction`, the share of rows `voiced` marks), so their default
melody was nearly empty, while on its fifteen sung tracks it voiced 33 to
70 %.

**A preference may set minimums for scalar outputs of its own measure,
`at_least`; a preferred measure below one is passed over for the next
preferred feature, or the content.** `melody` declares

```json
"prefers": [{"input": "stems/vocals", "parameters": {"bin_cents": 2},
             "at_least": {"voiced_fraction": "0.2"}},
            {"input": "stems-6s/vocals", "parameters": {"bin_cents": 2},
             "at_least": {"voiced_fraction": "0.2"}}]
```

The candidates are tried in that order: the 4-stem separator's vocal stem,
then the 6-stem separator's (`stems-6s`, where the operator made one), then
the mix; each stem must reach the same minimum, and a stem that falls short
is listed under `passed_over`. Either stem's melody is measured in 2-cent
bins.

`Bank.default_input` decides it, so the runner and every reader still agree:
a preference whose feature the bank holds is the default until its measure
of the feature is in the bank, and then only while each named scalar reaches
its minimum (decimal text, as parameters are written). A value that is not a
number is refused, not read either way. Without `--from`, the runner
measures the preferred feature as before; when that measure falls short, it
keeps the entry, measures the next default input too, and says what it
passed over (`passed_over`, each feature with its entry's key). A failed
measure is a failure, not a fall-back. `at_least` is not a parameter: no
entry's key or bits change, only which entry is a recording's default, so
`melody` stays version 3.

**Bass is its own category.** A melody never selects the bass part or a bass
stem by default: no feature `melody` prefers is a bass output (`stems/bass`,
`stems-6s/bass`), and a test holds the manifest to it. The bass line is read
by asking for it (`--from stems/bass`, `notes-basic-pitch` on that stem), never
as a recording's melody.

### Evidence for the amendment

The threshold sits between the two groups the rerun measured. With this
change, `extract.py run melody` on the eighteen tracks against the album's
scratch bank ran nothing (every entry was there) and chose:

| | tracks | vocal stem voiced (`voiced_fraction`) | default |
|---|---|---|---|
| instrumentals | 3 | 0.174, 0.071, 0.027 | the mix (5-cent bins; its melody voiced 0.66, 0.64, 0.65) |
| sung | 15 | 0.328 to 0.695 | the vocal stem (2-cent bins), as before |

The lowest sung track is 0.33, so 0.2 leaves a margin on both sides; it is
one album, and a quieter sung track would need the minimum checked again.

### Alternatives considered in the amendment

- **A level floor on the stem** (its loud level against the mix's), which
  the rerun also suggested. It was not measured; the voiced share is the
  thing a reader of the melody needs, and it separated the album's groups.
- **Falling back inside `melody.py`.** An extractor sees only the bits it is
  given; it cannot measure the mix while measuring the stem, and an entry
  that depended on another input would change what its key means.
- **A new `melody` version.** No output changes, so it would only re-key
  every melody entry.

## Amendment, 2026-10-19: a probe passes a stem over before it is measured

Status: proposed with the build of ADR 0112's step 0 (the `melody` item in
[docs/backlog.md](../backlog.md)); for the operator to confirm.

The backfill profile found `melody` running twice on every rehearsal it
measured: the vocal stem's measure fell short of its `at_least`, so the mix
was measured as well, and the first pass was spent only to learn that. A
rehearsal with nobody singing has a vocal stem of bleed, and `f0-pesto`,
which the backfill already runs on that stem for the vocal line, says so.

**A preference may name a `probe`: a column of a floating-point series that
another extractor measured of the preferred feature, a value, and the share
of rows that must reach it.** While the extractor's own measure of the
feature is not in the bank, and the probe's is and falls short, the
preference is passed over unmeasured; the runner names the probe's entry
under `probed_over`. Once the extractor's own measure is in the bank, it
alone decides, by `at_least`, so every bank that holds it answers as
before; a probe that is not in the bank says nothing. `Bank.default_input`
decides it, so the runner and every reader still agree. `melody` declares,
on both vocal stems,

```json
"probe": {"series": "f0-pesto/f0", "column": 1, "at_or_above": "0.5",
          "share_at_least": "0.03"}
```

Like `at_least`, a probe changes no key and no entry's bits, only which
entry is a recording's default, so `melody` stays version 3. The runner
never makes a probe: it saves the measure only where the caller ran
`f0-pesto --from stems/vocals` first, which the backfill's scheduler is to
do.

### Evidence for the second amendment

Twenty recordings have both a version 3 vocal-stem melody and `f0-pesto`'s
measure of the same stem: the album's eighteen tracks (`f0-pesto` version
1, whose confidence column version 2 left as it was) and the profile's two
rehearsals.

| | recordings | vocal stem voiced (`voiced_fraction`) | rows with confidence ≥ 0.5 |
|---|---|---|---|
| falls short | 5 | 0.027 to 0.174 | 0.000 to 0.024 |
| reaches | 15 | 0.328 to 0.695 | 0.118 to 0.685 |

A share of 0.03 sits above every stem that fell short and a quarter of the
lowest that reached, so on these recordings the probe passes over exactly
the stems `at_least` passes over. On copies of the profile's banks (x86-64
laptop, 4 threads), `extract.py run melody` with the probe ran once, on the
mix, and made the same entry as before, key and output digests alike:

| recording | before (vocal stem, then mix) | with the probe (mix) | saved |
|---|---|---|---|
| 2715 s rehearsal | 209 s | 159 s | 50 s (24%) |
| 554 s rehearsal | 62 s | 42 s | 20 s (32%) |

About 0.02 s per audio-second, some 2% of the backfill's compute where
nobody sings. A recording whose vocal stem reaches the minimum is measured
exactly as before, and the mix pass never ran there.

### Alternatives considered in the second amendment

- **`melody`'s own `measured_fraction` as a bound.** Voiced rows are a
  subset of measured rows, so it would be exact, but the two rehearsals'
  stems measured 0.22 and 0.28 of their rows: it never fires.
- **`f0-pesto`'s `loud_db`.** The two groups also part by level, but a
  quiet singer recorded low would be passed over; the confidence reads the
  voice itself.
- **A new scalar from `f0-pesto`.** It would need version 3 of `f0-pesto`
  and re-key every one of its entries, more compute than it saves.
- **Running the probe in the runner when it is missing.** `f0-pesto` costs
  more than the melody pass it would save; it pays only because it is run
  anyway.
