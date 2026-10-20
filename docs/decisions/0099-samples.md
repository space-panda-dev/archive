# 0099. A sample is a selection with a stem, a recipe and kinds, rendered on demand

Status: proposed (wave 2, drafted 2026-10-19)

The rehearsal review app is gaining a sample cutter, and a sample app will
browse what it cuts beside songbook songs and organised libraries of kicks
and snares. The archive can name an excerpt, but not a sample:

- An `archive.claim/audio_selection` version 2
  ([ADR 0048](0048-review-app-write-contracts.md), D-13) names the same
  moment in every source as tracks of sample spans. It cannot say *which
  signal* was cut (the bass stem of a rehearsal, channel 3 of a mixer's
  multitrack), nor what was done to it (a fade, a pitch drop, a reverse).
  A cutter that keeps those in its own state loses them; one that admits a
  rendered file for every tweak stores regenerable bytes as evidence.
- [ADR 0089](0089-practice-audio-and-exports.md) renders a recording's
  stems on request as derived media, but only whole recordings, mixed by
  gain, shifted and stretched. Nothing cuts, fades, reverses, or slices.
- [ADR 0098](0098-content-lineage.md) (proposed) lets an admitted file say
  what it was made from, with transforms that *describe*, and leaves "a
  transform recipe precise enough to render from" to this record.
- Nothing classifies a sample. `selection_kind` names what a rehearsal
  passage is (`original`, `talking`, …), not whether it is a one-shot or a
  loop, or a snare or a bass drum.
- A sample's tempo, key and length are wanted on every row of a sample
  browser, and nothing says where they come from.

The operator decided the following on 2026-10-19 (wave 2 decisions, the
earlier settled decisions this wave turns into contracts, W2.1 and W2.2);
this record states them as given.

- **A sample is `audio_selection` version 3**: a stem or channel, a
  transform recipe (gain, fade, pitch, stretch, reverse, slice) and sample
  kinds; rendered on demand as regenerable media (a generalisation of ADR
  0089's practice render: start/end, reverse, fades) and admitted only when
  chosen or sent. Cuts made in the review app and in the sample cutter are
  the same object.
- **W2.1 Sample classification has two axes.** Form: a small closed local
  SKOS scheme (one-shot, loop, break, phrase, texture, fx). Sound:
  MusicBrainz instrument IDs where they exist (bass drum, snare drum, …).
  Plus free tags as their own claim kind, never ranked.
- **W2.2 A sample's BPM, key and length** from the machine are views (bank
  series sliced over the selection's spans; regenerable, no records). A
  person's statement ("4 bars at 92") uses the existing meter-layer and key
  claims over the selection. No new field.

Packs, collections and source paths (W2.3, W2.4) are
[ADR 0100](0100-packs-and-collections.md)'s; the similarity index that
reads this record's facets (W2.6, W2.7) is
[ADR 0102](0102-retrieval-index.md)'s.

## Decision

### `archive.claim/audio_selection` version 3

**Version 3 is version 2 with a signal per span and an optional recipe.**
Its references and every version 2 rule are unchanged (tracks of ordered
`pieces`, one `piece` reference per span in track-then-span order, distinct
content across tracks, optional `alignment` pins), plus optional `stream`
references (below). Whether version 3's `alignment` pins may also name
`capture_alignment` version 2, which ADR 0107's aligner proposes, is that
record's Open 3; this draft keeps version 2's rule. Its payload:

```json
{
  "tracks": [
    {"pieces": [
      {"start_sample": 4410000, "end_sample": 4498200, "sample_rate": 44100,
       "stem": {"extractor": "stems", "key": "sha256:<stems entry key>",
                "output": "drums", "sha256": "sha256:<stem file digest>"}}
    ]},
    {"pieces": [
      {"start_sample": 4800000, "end_sample": 4896000, "sample_rate": 48000,
       "channel": 3}
    ]}
  ],
  "recipe": [
    {"op": "slice", "index": 0, "of": 4},
    {"op": "pitch", "cents": -200},
    {"op": "fade", "edge": "out", "samples": 2400, "shape": "equal_power"},
    {"op": "gain", "millibels": -300}
  ]
}
```

- **The signal (a stem or a channel)** is per span, because a stem is cited
  per recording and a track may roll over from one recording to the next.
  A span may carry at most one of:
  - `stem`: one `file` output of the bank, in exactly ADR 0079's citation
    shape (`check_stem_citation`), whose recording is that span's pinned
    Artifact. The stem is the recording
    ([ADR 0075](0075-stems-as-content.md), ADR 0089): the pin is what reach,
    lineage and destroy follow; the citation says which signal was cut;
  - `channel`: the 1-based index of one channel of a multichannel source,
    as a `channel` contribution names one
    ([ADR 0078](0078-contributions.md), D-17). Such a span needs a `stream`
    reference: an `archive.claim/audio_stream` version 1 revision about that
    span's Artifact revision, one per channel-bearing span in span order,
    and the index is at most its `channel_count`.

  A span with neither is the whole recorded signal, as in version 2. A
  stem that was itself admitted is pinned as its own Artifact instead.
- **The recipe** is an ordered list of 0 to 32 operations applied to one
  track's audio after its spans are joined. It uses exactly the closed
  vocabulary and ranges of ADR 0098's transforms, so an admitted render's
  lineage copies it verbatim, with two tightenings that make it
  prescriptive rather than descriptive: a `fade` names a `shape` of
  `linear`, `equal_power` or `exponential` (never `unknown`), and a `slice`
  names `of`. What each operation does:
  - `slice` (`index`, `of`): divide the audio so far into `of` (2 to 256)
    parts at samples `floor(i × n / of)`, and keep part `index`. Cutting a
    break at its transients is not a slice: each hit is its own selection,
    with its own spans;
  - `reverse`: play the audio so far backwards;
  - `gain` (`millibels`, -9600 to 2400);
  - `fade` (`edge` `in` or `out`, `samples`, `shape`), clamped to the
    audio's length at that step;
  - `pitch` (`cents`, -4800 to 4800), length kept. Unlike a practice render,
    a recipe shifts whatever it names, a drum one-shot included: a pitched
    kick is the point;
  - `stretch` (`ratio_ppm`, 100,000 to 10,000,000, output over input
    length), pitch kept. A speed change that moves pitch is a stretch and a
    pitch, as ADR 0098 says.

  An absent or empty recipe is the plain cut. Every number is an integer,
  so canonical JSON, and the render key below, has one spelling.
- **Kinds are claims about the selection, not payload fields** (below), as
  ADR 0048 made every selection fact its own contract ("one contract per
  fact"). See Open 1.

**One object for both cutters.** After the release that installs version 3,
the review app and the sample cutter both write version 3; a version 3
with no signal and no recipe means what a version 2 means. A version 2
selection stays valid and is a sample with neither. **Identity** follows
ADR 0044: correcting a sample's bounds, signal or recipe while keeping the
same intended sound is a revision of the same object; a variant kept
beside it (the same hit an octave down, on its own pad) is a new object
(Open 7).

**Door rules** (a specification release adds them with the bundle): the
version 2 rules; per span, at most one of `stem` (ADR 0079's closed shape)
and `channel` (an integer 1 to 64); exactly one `stream` reference per
channel-bearing span, in span order, pinning an audio stream version 1
whose `artifact` is that span's Artifact revision, with the channel at most
its `channel_count`; and a recipe of at most 32 operations from the closed
set, each with its fields and ranges, `index < of`. Two new profile rules
carry this: `x-archive-span-signals` and `x-archive-recipe`; the second
shares its operation table with ADR 0098's transform check. Door cannot
check that a cited stem exists; nothing in the archive can, and the
citation stays true when the file is gone (ADR 0079).

### Kinds: form, sound and tags (W2.1)

Three claim contracts, each with exactly one `subject`: an
`archive.claim/audio_selection` version 2 or 3 Claim revision, or an
Artifact revision when the whole file is the sample (a one-shot from a
library, Open 2); at most one `run` (a machine proposer's, ADR 0064); and
0 to 8 `derived_from` Claim revisions ([ADR 0079](0079-machine-harmony-claims.md)'s
role), such as the source-path claim a path proposer read (ADR 0100).

- **`archive.claim/sample_form` version 1**: payload `form`, one notation
  of the local SKOS scheme `arch:sample-form` that the specification ships
  beside `selection_kind`'s (P-11):

  | Notation | Concept |
  |---|---|
  | `one_shot` | a single event meant to be triggered: a hit, a note, a stab |
  | `loop` | a passage meant to repeat seamlessly, usually whole bars |
  | `break` | a drum passage, usually lifted from a performance, to loop or resequence |
  | `phrase` | a melodic, harmonic or spoken passage longer than one event, not meant to loop |
  | `texture` | sustained sound without a defined event or pulse: a pad, a drone, room tone |
  | `fx` | an effect or transition: a riser, a sweep, an impact |

- **`archive.claim/sample_sound` version 1**: payload `instruments`, 1 to 8
  distinct MusicBrainz instrument URIs (`https://musicbrainz.org/instrument/<MBID>`,
  checked as ADR 0078's are, `_check_instruments`), and optional `text`
  (1 to 100 characters), the asserter's own word. Where MusicBrainz has no
  instrument (a vinyl crackle), `text` stands alone and `instruments` may
  be absent; one of the two is required.
- **`archive.claim/sample_tag` version 1**: payload `tag`, 1 to 64
  characters, Unicode NFC, no control characters. One tag per claim, so a
  person withdraws one tag with one judgment (ADR 0081).

None is ranked or current. Disagreement stands side by side (INV-7): a
machine's `loop` beside a person's `break`. A view lists each claim with its
asserter and standing; it never counts tags to order them, and never keeps
the "most popular" form (W2.1, INV-8). A view that wants one form per row
names its rule, as `chords_current` does
([ADR 0082](0082-views-and-current.md)).

### Tempo, key and length are views (W2.2)

**`archive.view/sample_facets` version 1** answers, for one selection
revision and track (default the first) or one Artifact:

- `length`: exact, from the spans and the recipe alone (the joined span
  lengths, then each `slice`, then each `stretch` rounding half to even),
  in samples and the output rate; no bank needed (`sample_length`);
- `bpm`: from the bank's `rhythm` beats (or `beats-beatthis`), the events
  inside the spans, median interval, multiplied by the recipe's stretch
  ratios; `null` with fewer than four beats, which is every one-shot;
- `key`: the bank's `tonal` key regions overlapping the spans, or none,
  moved by the recipe's pitch: whole semitones by
  [ADR 0089](0089-practice-audio-and-exports.md)'s `transpose_key`, and a
  remainder of cents reported beside it, never rounded into the key;
- `loudness`: from `frames` `rms_db` over the spans, plus the recipe's
  gains;
- for a span with a `stem`, the features of that stem, which the bank keys
  and files as it does any stem's (ADR 0075); for a `channel`, none until an
  extractor measures single channels;
- each machine value with the bank entry key it came from, so it is
  regenerable and checkable, and `missing` naming the features not made.

Beside the machine values, the view lists every head `meter_layers` and
`key_regions` claim whose `source` is a span's Artifact and whose spans or
bars overlap it, with asserter and standing, never merged into the machine
value. "4 bars at 92" is a person's meter layers claim whose four bars lie
over the selection's samples on the source's timeline, with the tempo
derived from bar times as ADR 0077 derives it; a key is a key regions
claim. Both count the **source's** samples, before the recipe; the view
applies the recipe to report what the render sounds like (Open 9). A claim
about an admitted render pins the render's own Artifact instead.

Nothing here is stored: no field, no claim, no index of its own. ADR 0102's
index reads this view's answers.

### Sample renders: on demand, never evidence

**A sample request names everything that decides the file.** It is the
content-addressed form of one track of a selection, so a draft cut that is
not admitted (ADR 0044 keeps unaccepted drafts out of the archive) and an
admitted selection render the same way:

```json
{
  "pieces": [
    {"content": "sha256:<recording hex>", "start_sample": 4410000,
     "end_sample": 4498200, "sample_rate": 44100,
     "stem": {"extractor": "stems", "key": "sha256:…", "output": "drums", "sha256": "sha256:…"}}
  ],
  "recipe": [{"op": "reverse"}, {"op": "fade", "edge": "in", "samples": 441, "shape": "linear"}],
  "format": "flac",
  "renderer": {"id": "sample-render", "version": "1"}
}
```

- `pieces`: 1 to 64 spans of one track, each with its content digest and
  optional `stem` or `channel`, all at one rate (else `rates_differ`);
- `recipe`: the selection's;
- `format`: `flac` (default) or `wav`, since samplers often read only WAV;
- `renderer`: `{id, version}`, whose version stands for its code (ADR 0073).

`sample_request(selection, digests, track)` builds it from an admitted
selection revision and its pinned Artifacts' digests. The **sample key** is
the SHA-256 of the request's canonical JSON, defaults filled in
(`sample_key`), so two people's identical cuts share one file. The file is
filed by key, as ADR 0089 files exports, because it may read several
contents: `samples/<ab>/<cd>/<key hex>/sample.<flac|wav>` under the
derived-media root, with `request.json` beside it. Losing it loses nothing.

**The relay renders on request.** `POST /media/samples` takes a sample
request and answers `{key, status, media_type, bytes?, samples}`, with
`status` `ready` or `rendering`, as `POST /media/{digest}/practice` does;
`GET /media/samples/{key}` answers a ready render's bytes, with ranges, an
entity tag of its key, and private, immutable caching. Refusals:
`media_unknown_digest` (a content not in World's selected generation),
`media_forbidden`, `stems_not_made` and `feature_not_made` (as ADR 0089),
`channel_out_of_range`, `span_out_of_range` (beyond the stream's
`sample_count`), `rates_differ`, `renderer_unknown`, and `render_too_long`
when the joined spans exceed the configured `sample_max_seconds` (default
600). The relay renders one file at a time, in the background, never
separates stems, and builds no renderer by this record.

**Who may hear it is each source's answer.** A render is its sources in
another form, as a stem or a practice render is the recording (ADR 0089).
It is served only when, for every content it reads, ADR 0089's gate passes
(`practice_refusal`), read per audience as
[ADR 0093](0093-audiences.md) reads it: the caller is the active operator,
or every content is in the caller's readable set. When several Artifacts
share a content, the most restrictive answer applies (S-32). Playing a
render in a page is `listen`, offering it as a download is `redistribute`
([ADR 0097](0097-rights-by-use.md)); the relay records no disclosure per
request beyond what reaching the recording already recorded (ADR 0089).

**Rendering is the client's too.** As ADR 0089 keeps a live path, a cutter
may apply the recipe in the browser for an instant preview and ask for the
server render only when it needs the exact file. Both follow the same
operations; only the server's file can be admitted.

### Admitted only when chosen or sent

A cut kept in a library is its **selection**, admitted under `cut`; that is
the sample, and it is backed up with the recordings it pins. Its rendered
file stays regenerable. A render is **chosen** when a person asks to keep
the file itself (to load it on a sampler, drop it in a DAW project,
[ADR 0101](0101-daw-projects.md)), and **sent** when it goes to someone
outside the relay. Then, and only then, its exact bytes are admitted:

1. `POST /media/samples/{key}/stage` copies the ready render into the
   relay's staging repository for the caller and answers as
   `/documents/stage` does. The operator may always call it; a member needs
   an effective `admit` grant ([ADR 0094](0094-peoples-writes.md)), since
   it adds an Artifact (Open 5).
2. One admission carries, atomically: the selection version 3 if it is not
   yet admitted; the render's Artifact; and its
   [ADR 0098](0098-content-lineage.md) lineage claim, asserted by the
   caller, with the selection revision as its one `input`, `complete:
   true`, one lineage span from the selection's samples to the whole output
   with the recipe copied as its `transforms`, and `recipe: {"sample": <the
   sample request>}`, a third recipe form beside `bank` and `practice`
   (Open 3). Door recomputes the key from the request and checks that its
   pieces' contents are the selection's for some track.
3. A send then follows ADR 0089's order: admit, record the disclosure
   (ADR 0016), and only then let the bytes leave; beyond the circle, with a
   publication approval naming its use (ADR 0097).

The lineage claim caps the render's reach at its sources' (ADR 0098), and a
destroy of a source lists the render as a dependent. The operator's
complete lineage gives the render its sources' origin
([ADR 0096](0096-origin-of-recordings.md)); a member's leaves it unknown.

### Which abilities apply (ADR 0094)

| Write | Ability |
|---|---|
| a selection version 3, and the lineage of a chosen render | `cut` (version 3 joins version 2 in `MEMBER_CONTRACTS` under `cut`) |
| `sample_form`, `sample_sound`, `sample_tag`; meter and key claims over a sample | `annotate` |
| a chosen render's Artifact, through the stage route | `admit` |
| hearing a render, reading the views | none: reach decides |

A machine proposer (a path reader, a classifier) sends kinds with its run
through operator tools, never through the relay.

### World

- **Projection.** A version 3 selection projects as version 2, plus each
  span's stem citation as ADR 0079 projects one, its `channel` as
  `arch:channel_index` (the term a channel contribution already projects), and the recipe as an ordered list of operation resources
  under `arch:` terms shared with ADR 0098's transforms. `sample_form`
  projects its SKOS concept; `sample_sound` the MusicBrainz instrument URIs
  as they are, as contributions project them; `sample_tag` a literal.
- **`archive.view/samples` version 1** lists the samples the caller may
  read: by default every head selection with a signal, a recipe, or a kind
  claim, and every Artifact with a kind claim, and every selection or
  Artifact that is a member of a collection (ADR 0100), with `all` adding
  every other selection (Open 8). It filters by form, instrument, tag, collection
  (ADR 0100) and facet ranges, and answers each row's kinds as listed claims
  and its facets from `sample_facets`. Its readable set is the caller's
  (ADR 0093).
- **`derived`** (ADR 0098) already answers "used in" for a sample.
- `world/exports.py` holds the sample request, key, layout, length and
  facet arithmetic, with only the standard library, beside the practice
  rules it generalises.

## Consequences

- A cut is one record whichever app made it, and saying "the bass stem,
  bars 9 to 12, down a tone, faded out" costs a revision, not a file.
- A library of thousands of samples is thousands of small claims and no
  rendered bytes until someone chooses one; renders grow the derived-media
  root only with use, and may be deleted at any time.
- A bandmate cuts and classifies by default; keeping a rendered file needs
  `admit`, and loses nothing without it, because the selection already
  keeps the sample and its file can be made again.
- Version 3 brings a version cascade: `selection_kind`, `selection_title`,
  `artist_credit` and `song_occurrence` pin version 2 selections only
  (D-13), so a release that installs version 3 also installs versions of
  them that accept version 2 or 3, and a `performance_work` version that
  accepts the new occurrence (Open 6).
- Tempo and key appear for samples cut from analysed recordings and their
  stems; a library file shows them once the import runs the extractors
  (ADR 0100). One-shots have no tempo, by rule.
- No invariant changes. INV-7 and INV-8 hold for kinds; reach only narrows
  through lineage (INV-18).

## Alternatives considered

- **Admitting every render.** Regenerable bytes as permanent evidence,
  multiplied by every tweak; ADR 0089 rejected the same for stems.
- **A sample contract that pins a version 2 selection.** It splits one cut
  into two records the two apps would keep equal; the operator settled one
  object.
- **Kinds in the selection's payload.** Only the cut's author could state
  them, a machine's proposal could not stand beside a person's, and
  correcting a form would revise the cut.
- **One kind claim with form and sound together.** People routinely agree
  on one axis and not the other; one claim per axis lets each be judged.
- **Transient slicing in the recipe.** Onset positions belong to an
  analysis version; separate selections keep each hit exact and nameable.
- **BPM and key fields on the selection.** W2.2 forbids a new field; a
  stored value would go stale as the bank improves.
- **A render keyed by selection revision.** A draft has none, and two
  identical cuts would render twice.

## New nouns

**Sample** (a selection, or a whole Artifact, viewed as one).
1. Names what the sample app lists; it adds nothing a selection with its
   signal, recipe and kind claims does not hold.
2. None of its own; *cut* is the selection's.
3. None.
4. It is code inside an existing concept: a selection version and a view.
5. No.
6. A projection (`archive.view/samples`), not a subsystem. The word already
   means one audio sample value; the vocabulary entry says which, and code
   keeps `sample` for integers and `sample_` prefixes for this sense.

**Recipe** (and its *operation*).
1. Says, precisely enough to render, what is done to a cut; lineage
   transforms only describe.
2. None; the renderer applies it, under Enrichment's *derive*.
3. None.
4. It is a payload field of the selection, sharing ADR 0098's vocabulary.
5. No: part of an admitted claim, not state.
6. A detail.

**Signal** (a span's `stem` or `channel`).
1. Says which signal of a recording was cut.
2. None. 3. None. 4. A span field reusing ADR 0079's citation and ADR
   0078's channel index. 5. No. 6. A detail.

**Sample render**, **sample request**, **sample key**.
1. The file a recipe makes, and what names it.
2. None beyond *derive*, as for a practice render.
3. None.
4. They are derived files and their key, beside ADR 0089's.
5. No: regenerable.
6. A derived file.

**Sample form**, **sample sound**, **sample tag**.
1. Classify a sample on W2.1's two axes and by free words.
2. None; asserted and judged like any claim.
3. None of their own; INV-7 and INV-8 govern them.
4. They are claim contracts.
5. Their claims are evidence, as every claim is.
6. Contracts, not subsystems.

None of these is a new kind of Entity. Wave 2's new Entity kinds are
`collection` (ADR 0100, W2.3) and, if the operator accepts it, `daw_project`
(ADR 0101, its Open 1).

## Touch points

Verified on 2026-10-19 to exist, unless marked new:

- `world/exports.py`: `check_practice_request`, `practice_key`,
  `practice_refusal`, `transpose_key` (reused); new `check_sample_request`,
  `sample_request`, `sample_key`, `sample_path`, `sample_length`,
  `check_recipe`.
- `world/harmony.py`: `check_stem_citation` (span signals).
- `world/contributions.py`: `_check_instruments`, `INSTRUMENT` (sample
  sound).
- `world/circle.py`: `MEMBER_CONTRACTS` (selection version 3 under `cut`;
  the three kind contracts under `annotate`), `check_member_draft`.
- `world/grants.py` (new, ADR 0094): the ability table gains them.
- `world/documents.py`: `_pins` (a selection version 3 pins as version 2;
  the `sample` recipe through ADR 0098's edge).
- `world/views.py`: `check_request`, `catalogue`; `world/record_views.py`:
  new `samples` and `sample_facets` views.
- `world/contract_graphs.py`, `world/projection/6/vocabulary.ttl`: the
  version 3, recipe and kind graphs and `arch:` terms.
- `enrich/bank.py`: read by the facets view to find entries by key.
- `relay/relay.py`: route table (`/media/samples`, `/media/samples/{key}`,
  `/media/samples/{key}/stage`), configuration (`sample_max_seconds`);
  `relay/served.py`: beside `practice` and `practice_file`;
  `relay/practice.py`: `Renderer`, `render_path` (a sample renderer);
  `relay/staging.py`: `Documents.stage` for a render;
  `relay/refusals.py`: `channel_out_of_range`, `span_out_of_range`,
  `rates_differ`, `render_too_long`; `relay/openapi.json`; `relay/README.md`.
- `door/src/Archive/Door/Contracts.hs` (`x-archive-track-sample-spans`,
  reused) and `door/src/Archive/Door/SpanRules.hs`: new
  `x-archive-span-signals` and `x-archive-recipe`.
- A specification release under `spec/`: selection version 3, the three
  kind contracts, the `arch:sample-form` scheme, the selection-fact cascade,
  lineage's `sample` recipe; `docs/vocabulary.md` on acceptance.

## Open for the operator

1. **Kinds in the selection or beside it?** The settled decision lists
   "sample kinds" in selection version 3, and W2.4 speaks of "form and sound
   claims" a machine proposes "beside people's", which payload fields cannot
   do. *Recommend:* kinds are claims about the selection, as drafted; the
   selection carries signal and recipe only.
2. **May a kind claim's subject be a whole Artifact?** Otherwise importing
   a library mints one machine-made selection per file, and ADR 0044 meant a
   selection to be accepted audio. *Recommend:* yes, as drafted; ADR 0100
   decides what its import proposes.
3. **Lineage's `sample` recipe amends ADR 0098** (proposed, wave 1), whose
   recipe is closed to `bank` and `practice`. *Recommend:* add `sample` to
   lineage version 1 before it is installed, with Door recomputing the key.
4. **Which use does an admitted render read on its sources?** ADR 0097 reads
   `derive` through every lineage edge. A render of a pack's one-shot with
   no recipe is the one-shot redistributed as is, and would pass a pack's
   derive-yes, redistribute-no licence. This contradicts ADR 0097's intent
   and is not worked around here. *Recommend:* amend ADRs 0097 and 0098 so
   a lineage claim with a `bank`, `practice` or `sample` recipe is read as
   the source itself, with the exercised use, as ADR 0089 treats a stem;
   `derive` stays for lineage without a recipe (a bounce, a song).
5. **`admit` or `cut` for a chosen render?** ADR 0094 puts every Artifact
   under `admit`, default off. *Recommend:* keep `admit`; the selection
   already keeps a member's sample, so they lose nothing without it.
6. **The version cascade.** Selection facts, occurrences and
   `performance_work` need versions that accept version 3. *Recommend:*
   install them in the same release, each accepting version 2 or 3, and
   switch the review app to version 3 at that release.
7. **Recipe changes and identity.** *Recommend:* ADR 0044's rule, as
   drafted: a revision for the same intended sound, a new object for a kept
   variant; the cutter asks which when both are plausible.
8. **Which selections the samples view lists by default.** *Recommend:*
   those with a signal, a recipe or a kind claim, or in a collection
   (ADR 0100), with `all` for every selection.
9. **Meter and key over a transformed sample.** *Recommend:* claims count
   the source's samples before the recipe, and the view applies it; a claim
   about the sounding result pins an admitted render.
10. **Finding renders at destroy.** Renders filed by key join the existing
    question about exports. *Recommend:* the destroy's cleanup reads each
    `request.json` for the destroyed content, the same step that exports
    need.
11. **Tags beyond samples.** A notebook or recording may want free tags too.
    *Recommend:* keep `sample_tag` scoped to samples now; a general tag
    contract is a later decision if a second consumer appears.
12. **Render length cap.** *Recommend:* configurable `sample_max_seconds`,
    default 600; longer passages are practice renders.
