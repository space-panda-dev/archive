# 0080. Key regions and tuning are claims

Status: proposed

Nothing in an archive can say what key a passage is in, or where a
recording's pitch sits against the twelve-tone grid. Chord stacks and chord
sequences ([ADR 0077](0077-chord-stacks-and-meter.md)) hold pitch classes,
integers with C = 0, in twelve-tone equal temperament, and their computed
names use one fixed spelling (Eb, never D#; Gb, never F#), so a name can say
Gb where the music has an F#. The feature bank's `tonal` extractor
([ADRs 0073](0073-feature-bank.md) and [0074](0074-extractor-contract.md))
already measures a recording's tuning and its drift, a note collection with a
tonic and mode where the tonic leads, and a timeline of key regions, but as
regenerable features, never as claims. Three [open
questions](../open-questions.md) wait on this: how pitch names are spelled,
what contract holds a key or mode, and how a reading of a recording far from
A = 440 Hz is kept. JAMS `key_mode` has had nothing to export.

The operator decided the following on 2026-10-18 for specification 0.0.10;
this record states it as given.

- **D2.1, a key claim**: "a pitch collection + optional tonic + optional mode
  name, over regions (sample spans on a recording, sections on a Work).
  Spelling follows the asserted key."
- **D2.2, tuning**: "a claim about the stream (reference offset in cents +
  optional drift spans); readings stay 12-TET pitch classes relative to it."

The setting is the 0.0.10 aim: learning songs as lead sheets, showing and
correcting machine output, authoring lead sheets for the operator's own
songs, and sharing them with bandmates. A key is what a lead sheet's
signature and every chord's spelling follow, and two people, or a person and
a machine, may hear it differently; each asserts their own.

## Decision

**Three claim contracts, each one asserter's statement**, after the pattern
of ADR 0077: a timed one about a recording, an untimed one about a Work, and
a tuning about a recording. A key on a recording counts its samples; a key
on a Work has none, so, as with chord stacks and chord sequences, the two
are sibling contracts rather than one contract whose every rule branches on
its subject.

### Key readings

A **key reading** is one of three forms, as a chord reading is:

- a **key**: `collection`, 1 to 12 distinct pitch classes in ascending order,
  the notes the passage draws on; optional `tonic`, a pitch class of the
  collection; optional `mode`, 1 to 100 characters naming the mode, allowed
  only with a tonic; and optional `spelling`, the asserter's name for each
  class of the collection, in the collection's order;
- **no key**: `no_key` true, for an asserter saying a passage has no tonal
  centre, which differs from asserting nothing;
- **text only**: the source's `text` (a chart's "Key: Bb") when the asserter
  keeps it without interpreting it.

Every form may carry the source's verbatim `text` (1 to 200 characters) and,
exactly when the claim states a `weight_scale` (1 to 200 characters), a
`weight` as exact decimal text. As in ADR 0077, a region's `readings` are 1 to
16 distinct candidates in the asserter's order of preference, and a weight
compares readings within its own claim only (INV-8).

The collection is required and the tonic is not, because the collection is
what can be heard with the least interpretation: the notes of a passage that
could be either the dorian mode on one tonic or the major scale on another
are the same notes, and a machine that cannot tell which centre leads can
still say which notes. Such an asserter gives the collection alone, or the
two keys as two weighted readings. This is a producer's uncertainty in its
own terms: the `tonal` extractor's tonic margin is a weight on its own scale,
and a person's "either of these" is two readings with no weight.

**A named mode must name the notes.** When `mode` is one of `ionian`,
`dorian`, `phrygian`, `lydian`, `mixolydian`, `aeolian`, `locrian`, `harmonic
minor`, or `melodic minor`, the collection is that mode's on the tonic, or the
claim is refused. `major` is `ionian`. `minor` is any of `aeolian`, `harmonic
minor`, or `melodic minor`, because people say "minor" for all three. Any
other mode text (`blues`, a raga's name, a local name) is the asserter's own
and is not checked against the notes.

A `spelling` lists names in the collection's ascending order, not from the
tonic: a letter `A` to `G` and at most two sharps (`#`) or flats (`b`), each
sounding its class. `["Db", "Eb", "F", "Gb", "Ab", "Bb", "Cb"]` spells the
major scale on Gb; `["C#", "D#", "E#", "F#", "G#", "A#", "B"]` spells the same
classes as F# major.

### `archive.claim/key_sequence` version 1: keys over a Work, untimed

A Work's key ("in G, the bridge in B minor"), a chart's key signature, or a
person saying the coda goes up a tone describes a composition, not a
recording's samples. Such a claim pins at least one of a `work` (an Entity
revision of kind `musical_work`) and a `document` (the Artifact revision it
was read from), at most one `run` when a tool read it, zero to eight
`corrects` (key sequence version 1 Claim revisions), zero to eight
`derived_from` (below), and at most one **anchor**:

- `chord_sequence`, an `archive.claim/chord_sequence` version 1 or 2 Claim
  revision, whose entries the regions range over; or
- `form`, the form claim of ADR 0083, whose
  sections the regions range over.

Its payload has optional `part`, `weight_scale`, and `document_url` (as in
ADR 0077), and `regions`, 1 to 1,000 objects in the Work's order, each with
`readings`, an optional `section` (1 to 100 characters, the source's own
label), an optional `locator` (the document's `line`, a positive integer,
never going backwards), and, exactly when the claim pins an anchor, `first`
and `last`: 0-based indices of the anchor's entries or sections, with
`first` at most `last`, each region starting after the one before it ends.
Indices count the anchor as written, before any repeat is unrolled. A gap
between regions asserts nothing. With no anchor the regions are in order
only; a Work in one key is one region.

```json
{
  "payload": {
    "regions": [
      {"first": 0, "last": 15, "section": "Verse",
       "readings": [{"collection": [0, 2, 4, 6, 7, 9, 11], "tonic": 7, "mode": "major", "text": "G"}]},
      {"first": 16, "last": 23, "section": "Bridge",
       "readings": [{"collection": [1, 2, 4, 6, 7, 9, 11], "tonic": 11, "mode": "minor"}]}
    ]
  },
  "refs": [
    {"role": "work", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "chord_sequence", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

### `archive.claim/key_regions` version 1: keys over a recording

One asserter's keys over sample spans of one audio stream. It pins exactly
what a chord stacks claim pins for its timeline: exactly one `source`
Artifact revision, the mix; exactly one `stream`, an
`archive.claim/audio_stream` version 1 Claim revision about that same
Artifact revision, whose `sample_rate` the payload repeats and whose
`sample_count` no region ends past; at most one `run`; at most one
`document`. It may also pin:

- at most one `tuning`, an `archive.claim/tuning` version 1 Claim revision
  about the same `source` (below), the grid its pitch classes were measured
  on;
- at most one `sequence`, the key sequence version 1 Claim revision an
  alignment placed on this recording;
- zero to eight `corrects`, the key regions version 1 Claim revisions it
  corrects, under ADR 0077's rule for corrections: it replaces nothing, and a
  view shows the correcting reading first where the two overlap;
- zero to eight `derived_from`, the claims it was read from, in the role
  ADR 0079 gives chord, meter and note
  claims: a person who reads the key off their own corrected chords cites
  them.

Its payload has `sample_rate`, `regions`, and optional `part`,
`weight_scale`, `hold_rule`, `alignment`, and `document_url`, each meaning
what it means in a chord stacks claim. `regions` holds 1 to 1,000 objects of
`start_sample`, `end_sample`, `readings`, optional `end` (`asserted` or
`held`, with the claim's `hold_rule` exactly when a region is held), optional
`locator` (the document's `bar`), and optional `region` (allowed only when
the claim pins a `sequence`: the index of the sequence region it was placed
from), ordered and not overlapping. A region is the stretch a key holds;
where one key changes to the next, one region ends and the next begins.

```json
{
  "payload": {
    "sample_rate": 44100,
    "weight_scale": "tonic margin of the tonal extractor, version 4",
    "regions": [
      {"start_sample": 0, "end_sample": 882000, "readings": [
        {"collection": [0, 2, 3, 5, 7, 8, 10], "tonic": 5, "mode": "dorian", "weight": "0.02"},
        {"collection": [0, 2, 3, 5, 7, 8, 10], "tonic": 3, "mode": "ionian", "weight": "0.01"}]},
      {"start_sample": 882000, "end_sample": 1764000, "readings": [
        {"collection": [0, 1, 3, 5, 7, 8, 10], "weight": "0"}]}
    ]
  },
  "refs": [
    {"role": "source", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "stream", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "run", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

A key regions claim has no `analysed` reference. A key measured from a stem
cites the stem in its payload's optional `stem`, by bank key, exactly as
[ADR 0079](0079-machine-harmony-claims.md) defines it (`extractor`, `key`,
`output`, `sha256`, the digests written `sha256:<hex>`); it never needs the
stem admitted as an Artifact.

**From a Work to a recording**, as with chords: an alignment that places a
key sequence on a recording makes a separate key regions claim pinning the
`sequence`, the recording's `source` and `stream`, and its `alignment`, with
each region's `region` index and, since a sequence gives no times, held
ends. The sequence stays untouched, and one sequence may be placed on many
recordings.

**The mix and a part are separate claims.** A claim with no `part` is about
the whole sound; one with a `part` is about one player. A part that sounds
in another key from the mix, or from another part, is a difference to show,
never a conflict; the views that find it are
ADR 0082's.

### `archive.claim/tuning` version 1: where the grid lies

A tuning claim says where twelve-tone equal temperament sits on one audio
stream. It pins exactly one `source` and one `stream` as above, at most one
`run` (a machine measured it), at most one `document` (a source stated it:
"tuned to A = 432 Hz"), zero to eight `corrects` (tuning version 1 Claim
revisions), and zero to eight `derived_from` (ADR 0079's role). Its payload
has:

- `sample_rate`, equal to the stream's;
- `reference_cents`, exact decimal text, at least -50 and less than 50: the
  grid's offset from A = 440 Hz. A twelve-tone grid repeats every 100 cents,
  so this range names every grid once, and a recording a quarter-tone flat is
  `"-50"`. A = 432 Hz is about `"-31.77"`;
- optional `drift`, 1 to 10,000 spans of `start_sample`, `end_sample`, and
  `cents` (the same terms and range), ordered and not overlapping: stretches
  where the grid lies elsewhere, such as a tape running slow or a choir
  sinking. Outside every drift span the reference holds;
- optional `confidence_scale` (1 to 200 characters), and then `confidence`,
  exact decimal text, on the payload and on every drift span, compared only
  within that scale (INV-8);
- optional `document_url`, only with a pinned `document`;
- optional `stem`, the stem it was measured from, cited by bank key as on
  key regions.

```json
{
  "payload": {
    "sample_rate": 44100, "reference_cents": "-12.5",
    "confidence_scale": "tuning confidence of the tonal extractor, version 4", "confidence": "0.83",
    "drift": [{"start_sample": 0, "end_sample": 1323000, "cents": "-9", "confidence": "0.7"},
              {"start_sample": 1323000, "end_sample": 2646000, "cents": "-16", "confidence": "0.9"}]
  },
  "refs": [
    {"role": "source", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "stream", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "run", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

**Readings stay pitch classes, relative to the grid.** A chord stack's
classes, a key's collection, and a note's pitch (in
ADR 0086) name the nearest step of the
grid in force at their samples: a drift span's grid within it, the
reference elsewhere. So a band a quarter-tone flat still plays C, F, and G,
and no reading is rewritten in cents. A claim's pitch classes need no tuning
pinned to mean this; a key regions claim may pin the tuning it measured on,
and a note claim's cents are relative to one (ADR 0086). A grid this coarse
cannot hold a microtonal passage, whose notes fall between its steps; that
stays [open](../open-questions.md). An instrument tuned down a semitone, or
played with a capo, is not a tuning of the recording: it belongs to that
part's document fields, and the recording's classes are what sounds.

A tuning claim is not a key: the reference of a recording at A = 432 Hz says
nothing about its tonic.

### Spelling follows the asserted key

A spelling is a view, computed by World and never stored: given a key
reading, each of the twelve pitch classes has one name, by this stated rule.

1. A reading's own `spelling` names its collection's classes.
2. Otherwise a collection of seven classes takes one letter per class,
   ascending from the tonic (or, with no tonic, from its lowest class), with
   the fewest accidentals in all, then the fewest on the tonic, then flats
   over sharps: the major scale on Db (five flats), not on C# (seven sharps);
   on Gb, not F#, unless the asserter spelled it F#.
3. A class outside the collection is either a sharpened collection class
   below it or a flattened one above it, whichever has fewer accidentals; at a
   tie, flats where the collection's names use flats, otherwise sharps. In F
   major the class between F and G is Gb; in D major the class between G and
   A is G#.
4. Any class still unnamed, every class under a collection that is not
   heptatonic and not spelled, and every class with no key reading (or under
   `no_key` or text only) takes ADR 0077's default spelling.

A chord's name is then spelled by its key: the root takes the key's name for
its class, and the bass and each tone take the letter of their degree above
that root, so Gm(maj13)/D has an F#, a C7(#9) a D#, and D/F# keeps its F#
in any key. Which key reading spells a chord is the reader's choice: a key
claim whose region covers the chord's samples, or a lead sheet's pinned key
(ADR 0084); this record defines no current key, and a
reading with no key is spelled by the default. A source's own spelling of a
chord stays in its verbatim `text`, and a display may prefer it.

`world/tonality.py` gives `key_pitch_names`, `spell_stack`, and `spell_name`
(a computed name of `world/harmony.py` respelled under a key).

### Exchange is lossy and says so

World's view exports a key regions claim to JAMS `key_mode` as one
annotation of the readings at one rank, with the weight as confidence when
the claim's `weight_scale` is `probability`. JAMS holds a tonic, spelled with
at most one sharp or flat, and one of nine modes (`major`, `minor`, and the
seven diatonic modes), or `N` for no key. Harmonic and melodic minor are
exported as `minor`; another mode name exports the tonic alone; a reading with
no tonic is dropped, and its region is listed in the sandbox. The annotation
lists what it dropped, as ADR 0077's exports do:

| Format | Cannot express |
|---|---|
| JAMS `key_mode` | the collection, a reading with no tonic, alternative readings, mode names outside its nine, harmonic and melodic minor (exported as `minor`), a double accidental in the tonic's name, a held end, and weights on any scale but a probability |
| MusicXML `<key>` | regions in samples; it holds a key signature by `fifths` and `mode`, or a non-traditional signature by steps and alterations, so a collection exports only where it is a signature (not built) |

JAMS and MusicXML have no tuning annotation; a tuning claim is exported to
neither. ADR 0077's table row "JAMS `key_mode`: nothing to export yet" is
replaced by the row above.

### The rules live in World until a release installs them

`world/tonality.py` holds every rule above as World will re-check it when it
projects these claims, the spelling view, the grid in force at a sample, and
the JAMS view, reusing `world/harmony.py`'s reference and span checks. It
uses only the standard library, so an importer or extractor can check a
proposal before Door does. Door cannot admit any of the three contracts until
a specification release installs their bundles; the release adds the Door
profile rules this record needs: ascending distinct pitch classes, a tonic
within its collection, a checked mode's collection on its tonic, a spelling
whose names sound its classes, a key sequence's subject and at most one
anchor whose regions have indices exactly when it is pinned, a region's
`region` only with a pinned `sequence`, a tuning pinned by key regions being
about the same `source`, cents as exact decimals in [-50, 50), a confidence
exactly with its scale, and the existing sequential-span, held-end,
alignment, and same-subject rules.

**New nouns.** Key reading, pitch collection, tonic, mode, key sequence, key
region, tuning, grid, drift span, and spelling enter the
[vocabulary](../vocabulary.md). None is a subsystem: each names part of a
claim's payload or a view of one. They own no verb beyond Annotation's and
Enrichment's *assert* and *derive*, enforce no new invariant, and hold no
state outside the claims Door admits.

## Consequences

- A chart's key signature, a person's "the bridge is in B minor", a
  machine's note collections, and a detuned recording's grid can each be
  stated, attributed, and corrected, and none is lost when another is added.
- Chord names stop saying Gb where the key has F#, once a reader chooses a
  key; without one, nothing changes.
- The `tonal` extractor's `key_regions`, `keys`, `tuning_cents`, and
  `tuning_drift` outputs map onto these claims, with its margins as weights
  on its own scale; the mapping is ADR 0079's to propose.
- A lead sheet can pin the key it shows (ADR 0084), and a bandmate who hears
  the tune in another mode asserts their own key claim beside the
  operator's.
- No invariant changes. INV-7 keeps every reading, INV-8 keeps weights and
  confidences in their own claim's terms, and no rule ranks a person above a
  machine.

## Relation to other decisions

- [ADR 0005](0005-claims-review-and-authority.md): a key and a tuning are
  claims; disagreement is kept.
- [ADR 0042](0042-work-correspondence.md): a document URL is where it came
  from; the pinned Artifact is the evidence.
- [ADR 0064](0064-derivation-runs.md): a machine's claim pins its run.
- [ADR 0065](0065-audio-stream-facts.md) and
  [ADR 0066](0066-span-suggestions.md): integer samples of a pinned stream,
  ordered spans.
- [ADR 0077](0077-chord-stacks-and-meter.md): the same reading forms,
  weights, held ends, alignment from a sequence, and corrections; pitch
  classes are now relative to a tuning's grid, and computed names may be
  spelled by a key. Its contracts do not change.
- [ADR 0078](0078-contributions.md): `part` means what it means there; a key
  sequence is the `work` scope and key regions the `span` scope.
- [ADR 0079](0079-machine-harmony-claims.md): `derived_from` and the stem
  citation by bank key; the importer of the `tonal` extractor's key and
  tuning.
- [ADR 0081](0081-judgments-and-annotations.md): a judgment may scope
  to one region or one reading of a key claim.
- [ADR 0082](0082-views-and-current.md): differences between a part's key and the
  mix's are a computed view; any current key is a named rule there.
- [ADR 0083](0083-form-and-work-meter.md): the form claim whose sections a
  key sequence ranges over.
- [ADR 0084](0084-lead-sheets.md): a lead sheet pins the key it shows.
- [ADR 0086](0086-notes-and-melody.md): a note's cents are relative to
  a tuning claim's grid.

## Alternatives considered

- **A key as a tonic and a mode only.** A machine that hears the notes of a
  passage but not its centre, and a person who hears "the white notes, not
  sure where home is", would have to invent a tonic. The collection is the
  part both can assert.
- **A fixed list of keys** (24 major and minor keys). Modes, harmonic and
  melodic minor, pentatonic and blues collections, and passages with no
  tonal centre could not be stated.
- **One contract for both subjects**, with a `scope` as ADR 0078's
  contribution has. ADR 0077 rejected the same for chords: every rule of the
  timed contract would branch on the subject.
- **A key inside a chord stacks or sequence claim.** A key region spans many
  chords, is often asserted by someone who did not assert the chords, and is
  corrected on its own.
- **Spelling stored with each chord.** It repeats the key in every reading,
  and two spellings of one stack would look like two readings. A source's
  spelling is already kept verbatim in `text`.
- **Readings in cents or in hertz.** Every reading would change with the
  tuning, and two recordings of one Work in different tunings could not be
  compared by class.
- **Tuning folded into each claim** (a `tuning_cents` on chord stacks). Many
  claims would repeat one measurement and could disagree with each other
  without anyone asserting a different tuning.
- **An unbounded reference** (a recording a semitone flat as -100 cents).
  A grid 100 cents down is the same grid; the range would give one grid two
  readings, and a semitone-flat performance is a fact about the players'
  instruments, kept with their parts.
