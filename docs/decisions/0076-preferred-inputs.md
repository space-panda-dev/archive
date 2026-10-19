# 0076. An extractor may prefer a file feature of its content as its default input

Status: proposed (2026-10-18)

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
  The default is still the stem's; a reader that wants the lead line reads
  the content's own entry (`Bank.entry`, `--no-prefer`). Whether a default
  should fall back to the content when the preferred measure is empty is
  [open](../open-questions.md#interfaces-and-tooling).
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
