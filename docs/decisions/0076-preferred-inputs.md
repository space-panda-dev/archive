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
