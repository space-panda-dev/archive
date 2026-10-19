# 0079. Machine harmony becomes claims; stems are cited by bank key

Status: accepted (operator, 2026-10-18)

The feature bank measures a recording's chords, beats, key and tuning, and
separates its stems ([ADR 0073](0073-feature-bank.md),
[ADR 0074](0074-extractor-contract.md),
[ADR 0075](0075-stems-as-content.md)). None of it reaches the archive: a
feature is never admitted, and no tool proposes a claim from the bank. A
review client therefore has no machine chord or beat to show, correct, or
judge, and the contracts that would hold them
([ADR 0077](0077-chord-stacks-and-meter.md)) have three gaps that stop a
faithful proposal, and two more that stop the person correcting it:

- **A stem cannot be cited.** A chord or meter claim read from a stem pins
  it in `analysed`, which takes an Artifact revision. A stem is a bank file:
  regenerable, about 1.3 GB a recording-hour for four of them, and the
  recording itself, so admitting each one as evidence would make every
  separation permanent. A claim read from the bass stem can only pretend it
  was read from the mix.
- **A bass alone has no form.** The bass stem gives the lowest pitch class
  and nothing above it. A stack must state its `tones`, and an empty list
  already means that only the bass sounds, which is a different and stronger
  statement.
- **10,000 spans are too few.** The `chords` extractor cuts where the chord
  or the bass changes, as often as every half beat; a bass read per beat
  changes on every beat of a walking line. A three-hour rehearsal at 200
  beats a minute has 36,000 beats, while a beat-only layer may already hold
  100,000.
- **A person's reading cannot say what it was read from.** A machine's
  inputs are its run's `used` references
  ([ADR 0064](0064-derivation-runs.md)), but a person has no run, and
  `corrects` means that a view shows the new reading in place of the old.
  A person who writes a recording's chords after reading a machine's and a
  chart's has nowhere to say so.
- **Meter cannot be corrected.** Only chord stacks and chord sequences have
  `corrects`; a person who fixes a machine's beat grid asserts a claim that
  no view connects to the one it fixes.

The operator decided the following on 2026-10-18, in the design of
specification 0.0.10; this record states it as given.

- **D1.1** Stems are cited by bank key: the claim pins the mix Artifact and
  carries the stem's bank reference (extractor, key, output, SHA-256). Stems
  stay regenerable bank files; a stem is admitted as an Artifact only when a
  person chooses.
- **D1.2** A generic `derived_from` role of Claim revisions on chord, meter
  and note claims, usable without a run. Runs still record machine inputs.
- **D1.3** Bass-only is a new reading form, holding the bass and an
  optional root. Empty upper tones keep meaning "only the bass sounds".
- **D1.4** Bank to claims: proposed automatically by the capture import
  chain after window matching, and by an `enrich propose` backfill command.
  Proposals go through Door as matches do
  ([ADR 0072](0072-windowed-work-matches.md)).

The product this serves is learning songs as lead sheets, seeing and
correcting what machines heard, and sharing the result with bandmates. Mix
first still holds (ADR 0077): a claim read from a stem sits beside the mix's
claim and is never ranked above it by kind (INV-8).

## Decision

**Three new versions, each a superset of version 1.** Version 1 of each
contract stays admissible and unchanged, and every version 1 claim stays
valid under its own contract. A version 1 payload with version 1 references
is also a valid version 2 claim, so an importer that needs none of the
additions may write either.

| Contract | Version | Adds to version 1 |
|---|---|---|
| `archive.claim/chord_stacks` | 2 | `derived_from`; a `stem` cited by bank key; the bass-only reading; up to 100,000 spans; `corrects` may pin version 1 or 2; `sequence` may pin chord sequence version 1 or 2 |
| `archive.claim/chord_sequence` | 2 | `derived_from`; the bass-only reading; `corrects` may pin version 1 or 2 |
| `archive.claim/meter_layers` | 2 | `derived_from`; a `stem` cited by bank key (timed claims); `corrects` |

Meter layers version 2 is one version shared with ADR 0083, which adds to it
an untimed form (bars with no samples, about a Work), a `meter` reference to
the untimed claim an alignment placed, and each placed bar's `placed`. The
two records describe one contract version: a timed version 2 claim may use
the additions of both, an untimed one takes `corrects` and `derived_from`
from this record, and the release installs one bundle. World checks it in
one place, `world/form.py`'s `check_meter_layers_v2`.

**Chord sequence version 2 is chosen here, and the operator confirmed it.**
D1.2 puts `derived_from` on chord claims, and a chord sequence is the chord
claim a lead sheet of a Work pins (ADR 0084). A chart's "/E" over a bar,
with nothing above it written, is a bass-only reading as much as a bass
stem's is. So version 2 of chord sequence adds `derived_from`, the
bass-only reading, and `corrects` across versions, and nothing else: it has
no samples, so it cites no stem and keeps version 1's 10,000 entries. A
chord stacks version 2 claim may place a version 1 or a version 2 sequence;
version 1 stays able to place only version 1.

### A stem is cited by bank key (D1.1)

A version 2 claim's payload may hold `stem`, the bank reference of the one
stem it was read from:

```json
{
  "payload": {
    "sample_rate": 44100,
    "stem": {
      "extractor": "stems",
      "key": "sha256:<64 hex: the stems entry's key>",
      "output": "bass",
      "sha256": "sha256:<64 hex: the stem file's digest>"
    },
    "weight_scale": "lead of the strongest bass class over the next, bank bass proposer version 1",
    "spans": [
      {"start_sample": 0, "end_sample": 22050, "readings": [{"bass": 4, "weight": "0.62"}]},
      {"start_sample": 22050, "end_sample": 44100, "readings": [{"bass": 9, "weight": "0.48"}]}
    ]
  },
  "refs": [
    {"role": "source", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "stream", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "run", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

- `extractor` is the bank extractor's id (`[a-z][a-z0-9-]{0,62}`), `output`
  the name of its `file` output (`[a-z][a-z0-9_]{0,62}`), and `key` and
  `sha256` canonical SHA-256 digests, `sha256:` and 64 lower-case hex digits:
  the entry's key and the file's digest, exactly as `entry.json` writes them.
  The object is closed.
- **The `source` pin completes the reference.** The bank files every stem,
  and everything measured from one, under the recording's own content
  directory (ADR 0075). With the content digest of the pinned `source`
  Artifact revision, the citation names one file in any bank:
  `sha256/<ab>/<cd>/<source hex>/bank/<extractor>/<key hex>/`, output
  `output`, whose bytes have the digest `sha256`. Nothing else is needed to
  find it, to make it again (the key decides the method), or to tell whether
  a file made again is the one cited.
- **A citation is not evidence of the bytes.** The file may be deleted at
  any time (INV-4), and the citation stays true: it says which stem the
  asserter read. A stem made again by the same extractor version and
  parameters has the same key; whether it has the same bits, the digest
  says. A claim whose stem now differs still stands as its asserter's
  statement.
- **A claim cites a bank stem or pins `analysed` Artifacts, never both.**
  `analysed` keeps its version 1 meaning, audio a person chose to admit,
  and a claim read from one kind of input says so in one place.
- **The claim's samples are the source's.** A stem is separated at the
  separator's own rate (44,100 Hz for `stems` version 1) and lasts as long
  as the mix; spans and beats count samples of the pinned `stream`. That a
  stem shares the mix's timeline is the asserter's statement, as it is for
  an `analysed` Artifact, and the same
  [open question](../open-questions.md#door) covers both.
- **A run still records what it read.** A machine's claim pins its run, and
  the run's parameters list every bank entry read, including the stems
  entry's provenance, as ADRs 0073 and 0075 require. The `stem` field is
  what a reader of the claim needs without opening the run, and what a
  person's claim, which has no run, can give: a person who transcribes the
  bass while listening to the bass stem cites it the same way.
- **Any `file` output of the bank can be cited** the same way, with its own
  extractor, key and output. A practice render (ADR 0089) is derived media,
  not a bank output; a claim cites the stems it was read from.

**Admitting a chosen stem.** A person who wants a stem kept as evidence, for
example because it was sent to someone, admits its bytes as an Artifact
through an operator tool (the relay admits no content, ADR 0056, and its
document route refuses audio, [ADR 0085](0085-documents-lyrics-rights.md)). Its content digest is then the
citation's `sha256`, and later claims may pin it in `analysed`. This record
decides only that much. A claim that states what an admitted stem was
separated from, and by which method, is not defined: until a contract holds
it, the link from an admitted stem to its mix is the digest it shares with
the claims that cited it by key. How a chosen stem is admitted, and what
records its origin, is [open](../open-questions.md#claims-and-provenance).

### `derived_from`: what a claim was read from (D1.2)

A version 2 claim may pin zero to eight `derived_from` references, each an
exact Claim revision its asserter read this claim from, with or without a
run. It maps to `prov:wasDerivedFrom`.

- **It is lineage, and nothing else.** No view replaces, hides, or ranks a
  claim because another derives from it. It differs from the roles beside
  it: `corrects` says a view shows this reading first where the two
  overlap; `sequence` (and ADR 0083's `meter`) says this claim is a
  placement of that untimed claim; a run's `used` records what a machine
  read.
- **No revision is named twice**, and no revision is in both `corrects` and
  `derived_from`: correcting a claim already says it was read.
- **On these two contracts it pins** a Claim revision of a musical content
  contract: chord stacks (version 1 or 2), chord sequence version 1, meter
  layers (version 1 or 2), and the 0.0.10 contracts of ADRs 0080 (key
  sequence, key regions, tuning), 0083 (form, form spans) and 0086 (notes,
  melody, melody spans). A person who reads a recording's chords off their
  own corrected melody cites it; one who rewrites a machine's chords after
  reading a chart's cites both.
- **A machine's claim may carry it too.** Its run's `used` stays the record
  of its inputs; `derived_from` names, among them, the claims the output
  was read from, which a reader can follow without the run.

**The same role on the other contracts of 0.0.10.** ADR 0080's key regions,
key sequence and tuning, and ADR 0086's notes, melody and melody spans,
carry `derived_from` with this meaning, this count, and these two rules;
each contract states what it may pin. World's check is
`world/harmony.py`'s `check_derived_from`, which their modules share. ADR
0085's `document_source` uses the same role for the Artifact revisions a
document was made from: the PROV meaning is the same, and its targets are
its own. ADR 0084's lead sheet's `derived_from` pins only lead sheets.

**The stem citation on the other contracts.** A key regions or tuning claim
measured from a stem (ADR 0080) and a notes claim read from one (ADR 0086,
where a part's stem is the usual input) carry `stem` in exactly this shape,
under the same rules, including never beside an `analysed` Artifact. World's
check is `check_stem_citation`.

### The bass-only reading (D1.3)

A version 2 span's reading may be **bass-only**: an object with `bass`, a
pitch class, and no `tones`. It may carry `root` (a pitch class: the bass is
the bass of some chord on that root), the source's `text`, and, exactly when
the claim states a `weight_scale`, a `weight`. Nothing else: no `tones`,
`uncertain`, `voicing`, or `no_chord`.

```json
{"bass": 4}
{"bass": 4, "root": 0, "text": "/E", "weight": "0.62"}
```

- It says which pitch class is lowest and nothing about what sounds above
  it. A stack with `"tones": []` still says that the bass alone sounds;
  the two differ by the presence of `tones`, never by a flag.
- It is one of the span's readings, ranked and weighted like the others,
  and a span may hold it beside stacks: "C/E, or at least an E in the bass".
- **Names are views, and a bass names no chord.** No Harte string or
  lead-sheet symbol is computed for it; ADR 0082's `chord_names` view
  answers its form `bass_only` with no resolutions. JAMS has no bass-only
  value: `chord_harte` drops it and `chord` writes `X`, and the annotation
  lists "bass-only readings" among what it lost. The difference view cannot
  compare it tone by tone and counts it as not compared; the slash-bass step
  of ADR 0082's first current rule reads machines' bass-only claims from the
  bass stem.
- Chord stacks version 2 and chord sequence version 2 hold it. Version 1
  of either does not.

### 100,000 spans

A chord stacks version 2 claim holds 1 to 100,000 spans, the cap of a
beat-only layer's beats, so a bass read per beat fits in one claim wherever
the beats fit in one layer. A run whose output is longer splits it into
consecutive claims of one run, each with at most 100,000 spans; a gap still
asserts nothing, so a split needs no new field and loses nothing. At the
cap a payload is about 10 MB of JSON. Readings stay at 16 a span and bars at
10,000 a layer, which a three-hour recording at 200 beats a minute (9,000
bars of 4/4) does not reach.

### Meter corrections

A meter layers version 2 claim may pin zero to eight `corrects`, the meter
claims it corrects, of the same form: a timed claim corrects timed meter
layers version 1 or version 2 claims, and an untimed claim (ADR 0083)
untimed version 2 ones. It replaces nothing (ADR 0005).

World's view of meter corrections, computed and never stored, has one
stated rule. A correcting claim covers the samples from the earliest start
of its bars and beats to the latest end of its bars, or its last beat when
it has no bars; over those samples its layers are shown first and every
layer of the claims it names in `corrects` beside them, marked as corrected
by it. Elsewhere the corrected claim stands. Layers are not matched one to
one: a correction of a written layer that means to keep the corrected
claim's felt layer restates it. Where two claims correct one claim over the
same samples and disagree, or claims correct each other, the view reports
the conflict and chooses neither (INV-24), as ADR 0077's chord view does.

### `enrich propose` and the capture import step (D1.4)

This record decides the design and the interface; neither is built.

**Features become claims only through enrichment** (ADR 0073). A
*proposer* is a named method in `enrich/` that reads bank entries of one
recording and makes claims from them. It never runs an extractor and never
measures audio: what it reads is in the bank or it proposes nothing.

```
enrich propose --artifact <Artifact object id> [--stream <audio_stream object id>]
               [--proposers chords-jiang,chords-chordino,beats,bass] [--bank <root>]
               --archive <root> [--staging <repo>] [--dry-run] [--export <file>]
enrich propose --all [--proposers …] [--bank <root>] --archive <root> …
```

- `--artifact` names the mix; `--stream` chooses its audio stream claim when
  it has several, as `enrich detect` does, and the command refuses without
  one when there are several. `--all` proposes for every Artifact with an
  audio stream claim, one at a time: the backfill.
- `--bank` defaults to `ARCHIVE_FEATURE_BANK`; with neither, the command
  fails. Entries are found by the keys the installed extractors give
  (ADR 0073), never by searching.
- `--proposers` defaults to `chords-jiang,chords-chordino,beats,bass`.

The answer is JSON, as every `enrich` command prints, one object
per Artifact, with exit status 0, or 3 when nothing was proposed:

```json
{
  "status": "committed",
  "artifact": {"id": "urn:uuid:…", "revision_id": "sha256:…"},
  "proposers": [
    {"name": "chords", "status": "committed", "run": {"id": "urn:uuid:…", "revision_id": "sha256:…"}, "claims": 1},
    {"name": "beats", "status": "unchanged", "run": {"id": "urn:uuid:…", "revision_id": "sha256:…"}},
    {"name": "bass", "status": "nothing_proposed", "missing": ["stems/bass", "tonal/bass_chroma from stems/bass"]}
  ]
}
```

A proposer's `status` is `committed`, `predicted` (with `--dry-run`),
`unchanged`, or `nothing_proposed` with the features it lacked; the
command's is the strongest of its proposers'. A failed proposer fails the
command after the proposers before it have finished, and what they made is
kept.

**Each proposer makes one run and its claims, in one Door batch**
([ADR 0064](0064-derivation-runs.md)). The run's method is the proposer's
id and version; its `used` pins the mix Artifact revision and the stream
claim; its `parameters` Artifact holds the proposer's settings and, for each
feature read, the feature's name, its extractor's id and version, its
entry's key, the content it was filed under, and, for a `file` feature, the
file's digest; its `software` lists the proposer's and every read entry's
recorded software. Every claim is the run's output, pins it, and has the
tool's Package URL as its agent. **History stays quiet:** a run equivalent
to a recorded one (same method, parameters and inputs) proposes nothing and
answers `unchanged`. A new extractor version or a stem made again with other
bits is a new key or digest, so a new run, whose claims sit beside the old
ones; nothing is superseded by a machine.

**A proposer's id is its run's method.** [ADR 0082](0082-views-and-current.md)'s
first current rule ranks machines by their runs' method identifiers, so
each chord extractor has its own proposer, named as the extractor is, and
D4.2's order (Jiang before Chordino) is the order of those two ids. The two
learned chord extractors arrive with the feature bank's 0.0.10 work, which
retires the template `chords` extractor; a proposer reads only what its
extractor's installed version declares, and the other proposers' inputs
follow the installed versions in the same way.

**Times.** Bank spans and events are in seconds from the start of the
decoded stream, and the stream decoded is the first audio stream, which the
pinned stream claim describes. A time `t` becomes the sample
`round(t × sample_rate)` of the pinned stream (half to even), clamped to `0`
and `sample_count`. A span that becomes empty is dropped; a shared boundary
stays shared, since one time rounds to one sample; events that land on one
sample are one.

**The proposers.**

| Proposer | Reads | Proposes |
|---|---|---|
| `chords-jiang` | the mix's `chords-jiang/chords` spans and its `readings` file | one chord stacks version 2 claim. Each span is a span whose end is `asserted`; its readings are the extractor's weighted candidates in its order, each read from its label: the root as `root`, the quality's intervals as `tones` above the bass, the `/bass` as `bass` (the root when none), the label verbatim as `text`, and `N` as `no_chord`. Weights are the extractor's own, as exact decimal text, under the scale it names. A later candidate equal to an earlier one is dropped. |
| `chords-chordino` | the mix's `chords-chordino/chords` spans | one chord stacks version 2 claim, one reading a span, read from its label as above, with no weights. |
| `beats` | the mix's `rhythm/pulse` and `rhythm/downbeats` | one meter layers version 2 claim with a beat-only layer of the pulse, with no `downbeats` (the tracker publishes them only where its margin is clear, and a layer's `downbeats` would assert every other beat is not one), and, when any downbeat is published, a bars layer: a bar from each published downbeat to the pulse its value of beats later, in the meter its label states, with the pulses inside it as its `beats` when their count is the numerator or divides it evenly (as equal groups), and none otherwise. Neither layer has a `role`: a tracker does not know the written meter from the felt one. |
| `bass` | `stems/bass` of the mix, `tonal/bass_chroma` and `tonal/pitched` measured on that stem (`extract.py run tonal --from stems/bass`), and the mix's `rhythm/pulse` | one chord stacks version 2 claim citing the bass stem in `stem`. For each interval between two pulses, the strongest class of the stem's mean bass chroma over it, when the interval is pitched for at least the share its parameters state and the class leads the next by at least their margin; otherwise no span. Each span holds one bass-only reading weighted by that lead; neighbouring intervals with the same bass are one span. |
| `key` | the mix's `tonal/key_regions` | one key regions claim (ADR 0080): a region per span, its reading the label's tonic and mode when the label names them and its collection otherwise, weighted by the tonic margin on the extractor's scale. |
| `tuning` | the mix's `tonal/tuning_cents`, `tuning_confidence` and `tuning_drift` | one tuning claim (ADR 0080): the reference brought into [-50, 50) cents, its confidence on the extractor's scale, and drift spans where windows depart from the reference by more than the parameters' minimum, merged. |
| `notes` | `melody/notes` of the mix or of a stem | one notes claim (ADR 0086) per part, `part` the stem's output or `mix`, citing the stem when there is one. |
| `sections` | the mix's `structure/sections` | one form spans claim (ADR 0083) with the extractor's labels. |

The last four propose the contracts of other records of 0.0.10 and run only
when named in `--proposers`, so the import proposes chords, beats and bass
by default.

**The capture import's fifth step.** After `enrich match-windows`
([ADR 0070](0070-capture-import.md), [ADR 0072](0072-windowed-work-matches.md)),
when a bank root is configured, the import makes the mix's chord entries
(`chords-jiang`, `chords-chordino`) and the entries the default proposers
need (`frames`, `rhythm`, `tonal`) for each Artifact of each session's primary source, then runs
`enrich propose` on it with the default proposers. It does not separate stems:
separation takes most of an hour a recording-hour (ADR 0075), so `bass`
answers `nothing_proposed` until the operator, or a worker, makes the stems,
and a later `enrich propose --all` proposes what has become available. A
recording that is already proposed answers `unchanged`. Without a bank root
the step is reported as skipped, not failed; a failure stops the chain once
the step has finished, as every other step does. The import report keeps
each answer. Running the import against an archive stays an operator command
under ADR 0061's standing approval for scratch archives.

### What Door and World need

`world/harmony.py` holds every rule above as World re-checks it:
`check_chord_stacks_v2`, `check_chord_sequence_v2`, `check_reading_v2`,
`is_bass_only`, `check_stem_citation`, `check_derived_from`, and
`check_lineage` (the two together, with the rule that a stem and `analysed`
exclude each other). Meter layers version 2, timed and untimed, is checked
by `world/form.py`'s `check_meter_layers_v2`, which applies these. Version 1's
checks are unchanged and refuse every addition. The module still uses only
the standard library, so a proposer checks its claims before Door does.

Door cannot admit any version 2 until a specification release installs
its bundle. The release adds these profile rules: a span cap of 100,000; a
reading that is a stack, no chord, text only, or bass-only, and a bass-only
reading's closed fields; the closed `stem` object with its patterns; a
payload `stem` only when the `analysed` role is empty; `derived_from` of 0
to 8 Claim revisions of the contracts listed above, distinct, and disjoint
from `corrects`; `corrects` targets of chord stacks version 1 or 2, of
chord sequence version 1 or 2, and of meter layers of the same form; a
chord stacks version 2 `sequence` of chord sequence version 1 or 2; and
every rule of version 1. That a stem's
file has the cited digest, and shares the mix's timeline, are not Door's to
check: the bank is not in the archive.

**New nouns.** Bank citation (a stem cited by bank key), bass-only reading,
derived from, and proposer enter the [vocabulary](../vocabulary.md). None is
a subsystem: the first three name part of a claim's payload or references,
and a proposer is an enrichment method in `enrich/`, whose verb is
Enrichment's *derive*. None enforces a new invariant or holds authoritative
state outside the claims Door admits.

## Consequences

- A new recording reaches a review client with machine chords, a beat grid,
  and, once stems exist, a bass line, each a claim with its run. A person
  corrects any of them with a claim of their own that cites what it
  corrects; nothing is overwritten (INV-7).
- Stems stay in the bank. No claim needs one admitted, and a bank lost
  entirely loses no evidence: the claims, their runs, and their citations
  remain, and the stems can be made again and checked against their
  digests.
- [ADR 0082](0082-views-and-current.md)'s slash-bass rule has its input: machines' bass-only claims from
  the bass stem, beside the mix's chords.
- A person's reading says what it was read from, so a later reader can
  follow it to the machine claims and documents behind it without a run.
- A machine's chords over a three-hour rehearsal fit in one claim; a longer
  recording is split into claims of one run.
- The import does more work per recording when a bank is configured, a few
  minutes a recording-hour beside the minutes of window matching; separation
  stays the operator's or a worker's.
- Admitting a chosen stem leaves its origin unstated until a contract holds
  it.
- No invariant changes. INV-8 keeps every weight in its own claim's scale
  and ranks no reading by its asserter's kind; INV-4 holds because a
  citation names regenerable bytes and depends on none of them.

## Relation to other decisions

- [ADR 0005](0005-claims-review-and-authority.md): a correction is a claim;
  disagreement is kept.
- [ADR 0064](0064-derivation-runs.md): each proposer's output pins its run,
  in one batch with it; `derived_from` never replaces a run's `used`.
- [ADR 0066](0066-span-suggestions.md): integer samples of a pinned stream.
- [ADR 0070](0070-capture-import.md) and
  [ADR 0072](0072-windowed-work-matches.md): the import chain gains a step
  after window matching, and proposals go through Door as matches do.
- [ADR 0073](0073-feature-bank.md), [ADR 0074](0074-extractor-contract.md)
  and [ADR 0075](0075-stems-as-content.md): a feature is never admitted, a
  stem is filed under its recording, and a run records the entries it read;
  a citation names an entry by the key the bank gives it.
- [ADR 0077](0077-chord-stacks-and-meter.md): version 1 is unchanged;
  version 2 keeps every rule and adds the above.
- [ADR 0078](0078-contributions.md): a chord stacks claim may still pin the
  contribution saying whose part it describes.
- [ADR 0080](0080-key-and-tuning.md): key and tuning claims carry `derived_from` and `stem` as here;
  the `key` and `tuning` proposers make them.
- [ADR 0081](0081-judgments-and-annotations.md): a judgment may scope to one span or reading of a version 2
  claim, which keeps version 1's spans and readings.
- [ADR 0082](0082-views-and-current.md): its views read chord stacks version 1 and 2; `bass_only` is a
  reading form, and the slash-bass rule reads bass-only claims.
- [ADR 0083](0083-form-and-work-meter.md): one meter layers version 2 is shared; form spans come through
  the `sections` proposer.
- [ADR 0084](0084-lead-sheets.md): a lead sheet may pin chord stacks version 2.
- [ADR 0086](0086-notes-and-melody.md): notes claims carry `derived_from` and `stem` as here; the
  `notes` proposer makes them.
- [ADR 0089](0089-practice-audio-and-exports.md): its practice request names a stems entry by the same
  extractor and key; a chosen stem or render is admitted as above, its
  origin open.

## Rejected

- **Admitting every stem as an Artifact.** Every separation would become
  permanent evidence at about 1.3 GB a recording-hour, a new separator
  version would double it, and a stem is the recording, so each copy is one
  more thing to destroy with it. D1.1 admits a stem only when a person
  chooses.
- **Recording the stem only in the run.** A person's claim has no run, and
  every reader would have to open the run's parameters to learn that a
  claim describes the bass stem rather than the mix.
- **The stem as a reference role.** A reference pins an archive object
  (D-3); a bank entry is not one and must not become one to be cited.
- **The stem's digest alone.** The bank files entries by key, not by output
  digest, so a digest cannot be found or made again; the key alone cannot
  tell a file made again with other bits from the one read.
- **A bass-only reading as a stack with a flag, or with `tones` set to
  null.** Each makes every reader of a stack check one more field before
  trusting `tones`; the absent field is the statement, and the shape cannot
  be mistaken for a stack that has `tones`.
- **A bass as uncertain tones over every other class.** Eleven `maybe`
  classes say each may sound, which is a claim about each; bass-only says
  nothing about them.
- **A separate bass line contract.** A bass reading is a reading of the same
  span as the chord heard over it; another contract would put the slash
  bass, its corrections, and its judgments in a second place.
- **`corrects` for lineage.** A view replaces what is corrected; a person
  who read a chart to write their chords has not corrected the chart.
- **Splitting by a stated range,** as ADR 0086's notes do. A notes claim
  needs its range to assert silence inside it; a gap in a chord claim
  already asserts nothing, so a split needs no field, and a higher cap keeps
  one run's chords of an ordinary recording in one claim.
- **Extractors that propose claims themselves.** An extractor knows content,
  not archives (ADR 0074); it would need the archive's identifiers, Door,
  and the stream claim, and its output would stop being regenerable.
- **Proposing only on request.** D1.4 has the import propose, so a new
  recording arrives with something to correct.
