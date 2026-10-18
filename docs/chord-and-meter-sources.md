# Chord and meter sources

How the first importers of outside sources will assert chord stacks and meter
layers ([ADR 0077](decisions/0077-chord-stacks-and-meter.md)). This is a
design; neither importer exists, and neither can admit anything until a
specification release installs the two contracts.

## What every source claim has

An outside source says what a piece contains in its own terms: bars, beats,
symbols, and notes in a written order. A recording is a different thing, so
a source claim is always two assertions joined: the document says this, and
it falls at these samples of this recording.

1. **The document is evidence first.** Its bytes are admitted as an Artifact
   and the claim pins it in role `document`. Where it was retrieved from goes
   in `document_url`; the URL is never the evidence
   ([ADR 0042](decisions/0042-work-correspondence.md)). The document's author,
   when known, is a claim about that Artifact, not a field of the chord claim.
2. **The claim's `agent` is whoever made this assertion.** A person importing
   and placing chords by ear is the agent, with alignment `method` `manual`.
   An importer that aligns by itself is a tool with a derivation run
   ([ADR 0064](decisions/0064-derivation-runs.md)); its run pins the document
   and the audio in `used`, and its configuration is its `parameters`
   Artifact. The `alignment` names the aligning method and, when it can bound
   it, `max_error_samples`.
3. **The source's words stay verbatim.** Every chord symbol goes in its
   reading's `text`, exactly as written. Interpreting it as a stack is a
   further reading by the importer's grammar, whose version is part of its
   method; an importer that cannot interpret a symbol keeps a text-only
   reading rather than guessing. A better grammar later makes a new claim
   beside the old one.
4. **Locators keep the document's numbering.** A bar and a span carry the
   document's own bar number (and a chord's beat). A repeat played twice
   gives two spans with the same locator at different samples: the claim
   follows the performance, the locator points back into the document.
5. **Nothing replaces anything.** A chart's claim, a transcription's claim, a
   listener's claim, and a machine's claim over the same seconds all stay,
   each with its own provenance (INV-7). A newer import of a revised document
   is another claim pinning the other Artifact.

## A Guitar Pro importer

A Guitar Pro file holds a master bar list (time signatures, beaming, repeats,
and alternate endings), a tempo map, and one track per instrument with its
tuning, notes, and optional chord names and diagrams per beat.

- **Meter.** One `meter_layers` claim per document, with one layer for the
  master bars. Each bar takes its numerator and
  denominator; a beaming of 2+2+3 eighths becomes `grouping`
  `[2, 2, 3]`. Bar start samples come from the alignment; beat samples are
  given only where the alignment places them, never filled in from the tempo
  map, which describes the file's playback, not the recording. A tempo or
  time-signature change is a new bar's values; a written metric modulation
  becomes a `modulations` link with its note values and ratio. Repeats and
  endings are unrolled into the performed order.
- **Chords the transcriber named.** One `chord_stacks` claim whose readings
  keep the chord name as `text` and, where a chord diagram is given, a
  `voicing` computed from the frets and the track's tuning; its lowest note
  is the bass.
- **Chords the notes imply.** One `chord_stacks` claim per pitched track,
  with that track as `part`: at each change of the sounding note set, a stack
  of those notes with their `voicing`, and no root (the notes do not name
  one). These are what the future comparison of players' parts reads.
- **Alignment.** First a person, tapping the downbeat of each section in a
  review client (`manual`); later a run that renders the file and aligns it to
  the mix's chroma, which records its method and parameters.

## A chart importer

A chart is a page of chord symbols, from a website or a saved file: symbols
over lyrics with no bars, or a bar grid (as in an iReal Pro or ChordPro
chart) with a stated meter.

- **The page is the document.** The saved bytes are admitted, as retrieved;
  a page that changes later is another document.
- **Chords.** One `chord_stacks` claim per chart. Each symbol's text is kept
  verbatim; the importer's grammar turns it into a stack: the slash bass as
  `bass`, the symbol's root as `root`, and the quality's tones as `tones`,
  with no voicing, since a chart does not give one. "N.C." is a `no_chord`
  reading. A symbol the grammar cannot read is a text-only reading.
- **Meter.** Only a chart with a bar grid asserts a `meter_layers` claim,
  with its bar numbers as locators. A chart without bars asserts no meter,
  and its chords carry no locator.
- **Alignment.** A chart has no times of its own, so every span's samples
  come from the alignment: a person placing each change (`manual`), or a run
  that aligns the chart's sequence to the mix's machine chord claims and
  records how.

## What the importers do not decide

Which of several claims is current, how two asserters' beat grids are matched
against each other, and how pitch names are spelled are
[open questions](open-questions.md#claims-and-provenance). An importer asserts;
it never chooses between its claim and another.
