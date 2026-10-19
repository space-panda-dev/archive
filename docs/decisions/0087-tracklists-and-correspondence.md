# 0087. Releases list their tracks; artists, releases and recordings correspond to MusicBrainz and Discogs

Status: proposed

Specification 0.0.9 installed two Entity kinds after MusicBrainz:
`release`, one edition of music as issued, and `recording`
([ADR 0078](0078-contributions.md), spec D-8). Credits can be scoped to
either. Three things are still missing:

- **Nothing says which recordings a release carries.** A release credit
  ("produced by", "mastered by", a band member on bass throughout) never
  reaches a track. ADR 0078's resolved view says so outright: release to
  recording containment is not used, because no contract states it.
- **Nothing ties a `recording` Entity to audio in the archive.** A rip of a
  CD's third track is an Artifact; a take the operator later released is an
  `audio_selection` or `song_occurrence` version 2 Claim. ADR 0078 lets a
  recording-scope credit pin any of the three, but nothing says that an
  Artifact and a recording Entity are the same recording, so credits and
  readings on each stay apart.
- **Only Works correspond to an outside catalogue.** `archive.claim/work_musicbrainz_correspondence`
  version 1 ([ADR 0042](0042-work-correspondence.md), spec D-11) maps a
  `musical_work` to a MusicBrainz Work. An importer that meets a MusicBrainz
  artist, release, or recording, or any Discogs entry, cannot recognise the
  Entity it made last time except by the operator's choice
  ([open questions](../open-questions.md#door)).

The operator decided the following on 2026-10-18, in the design of
specification 0.0.10; this record states it as given.

- **D9.1.** A tracklist: a release claim with ordered disc and position, each
  pointing to a recording Entity. Plus correspondence claims linking
  artists, releases, and recordings to MusicBrainz and Discogs (masters and
  releases), modelled on ADR 0042.

The product this serves is learning songs as lead sheets, correcting what
machines heard, authoring lead sheets for the operator's own songs, and
sharing them with bandmates. A lead sheet of a released song
(ADR 0084) is of a Work, often for one recording; the
contracts here let that recording be found on the release a bandmate owns,
and let its credits come with it.

## Decision

Three new claim contracts, each version 1. None defines `current`, none
replaces an earlier contract, and ADR 0042's Work correspondence stays as it
is. Their rules are in `world/catalogue.py`, which reuses ADR 0078's
reference and provenance checks from `world/contributions.py`.

### The asserter, as in ADRs 0077 and 0078

Each of the three contracts may pin at most one `run` (a
`derivation_run` version 1 Claim revision, which a machine's claim pins) and
at most one `document` (the Artifact revision of an outside source's bytes:
a saved MusicBrainz or Discogs response, a scan of a sleeve). Their payloads
may carry ADR 0078's asserter fields with ADR 0078's rules: `document_url`,
`retrieved_at`, and `locator` only with a `document`; `confidence` (`scale`
and exact decimal `value`, compared only within its scale, INV-8); and
`text` (1 to 1,000 characters, the source's wording). Unlike a contribution,
a document does not require `text`: a tracklist's titles and numbers are
already the document's wording. A person's claim pins neither and names the
person as its envelope `agent`. As ADR 0042 says, a URL is never the
evidence: an imported claim pins the saved response it was read from.

### `archive.claim/tracklist` version 1

One asserter's statement of a release's media and tracks, in order, and
which recording each track carries.

| Role | Count | Pins |
|---|---|---|
| `release` | exactly 1 | an Entity revision of kind `release` |
| `track` | 1 to 4,000 | an Entity revision of kind `recording`, one per track, in payload order |
| `run` | 0 or 1 | a `derivation_run` version 1 Claim revision |
| `document` | 0 or 1 | an Artifact revision |

The payload is closed. It holds `media` and may hold `partial` and the
asserter fields above.

- `media`: 1 to 200 media (MusicBrainz's discs, sides of a set, or a digital
  medium), each a closed object of `position` (an integer of at least 1),
  `tracks`, and optional `format` (1 to 100 characters, as the source names
  it: "CD", "12\" Vinyl", "Digital Media") and `title` (1 to 500
  characters, a disc's own title). Media positions are strictly ascending.
- `tracks`: 1 to 999 tracks per medium, each a closed object of `position`
  (an integer 0 to 999) and optional `number` (1 to 20 characters, the
  source's printed number: "A1", "3"), `title` and `artist_text` (each 1 to
  500 characters, the track's title and artist credit as the source prints
  them), and `length_ms` (an integer of at least 1, the length the source
  states, not a measurement). Track positions within a medium are strictly
  ascending. Position 0 is MusicBrainz's hidden pregap track and can only
  come first.
- **One `track` reference per track, in order.** The references with role
  `track` are aligned by index with the tracks of every medium in turn, as
  ADR 0045 aligns a selection's pieces with its `piece` references. The
  total is at most 4,000 and equals the number of tracks. The same recording
  may be pinned by several tracks: an album version that recurs on a bonus
  disc.
- **Complete unless it says otherwise.** Without `partial`, media are
  numbered 1, 2, … in order and each medium's tracks run 0 or 1, then on with
  no gap: the list claims to be the whole release. `partial` is `true` when
  the asserter lists only some tracks ("track 3 of side B is this
  recording"); positions then only ascend. `partial: false` is refused: it
  is the default.

A tracklist carries no release title, date, label, barcode, or catalogue
number. The release Entity's `label` names it for display; release events
and identifiers are left out on purpose until a decision needs them.

```json
{
  "agent": "urn:uuid:…(a person, or the importer's process)",
  "payload": {
    "media": [
      {"position": 1, "format": "CD", "tracks": [
        {"position": 1, "number": "1", "title": "First Song", "length_ms": 215000},
        {"position": 2, "number": "2", "title": "Second Song"}
      ]},
      {"position": 2, "format": "CD", "title": "Bonus Disc", "tracks": [
        {"position": 1, "number": "1", "title": "Third Song", "artist_text": "The Band feat. A Guest"},
        {"position": 2, "number": "2", "title": "First Song (single edit)"}
      ]}
    ],
    "document_url": "https://example.org/ws/release/…",
    "retrieved_at": "2026-10-18"
  },
  "refs": [
    {"role": "release", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "track", "id": "urn:uuid:…(recording 1)", "revision_id": "sha256:…"},
    {"role": "track", "id": "urn:uuid:…(recording 2)", "revision_id": "sha256:…"},
    {"role": "track", "id": "urn:uuid:…(recording 3)", "revision_id": "sha256:…"},
    {"role": "track", "id": "urn:uuid:…(recording 1 again)", "revision_id": "sha256:…"},
    {"role": "document", "id": "urn:uuid:…(the saved response)", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

Two sources that disagree about a release's tracks (a vinyl side order, a
bonus track one pressing lacks) are two tracklist claims, both kept and both
shown. A release that differs in its tracks is, by MusicBrainz's own rule,
another release, so most disagreements are a sign that two editions were
merged into one Entity: Review sees both lists and the operator can make a
second release Entity. Asking for the current tracklist is unsupported.

### `archive.claim/recording_audio` version 1

Audio in the archive is a recording Entity, whole or in part. This is the
one link between the catalogue's recordings and the archive's own audio.

| Role | Count | Pins |
|---|---|---|
| `recording` | exactly 1 | an Entity revision of kind `recording` |
| `audio` | exactly 1 | an Artifact revision (a whole file), or an `audio_selection` or `song_occurrence` version 2 Claim revision (samples of one or more files, as ADR 0078's recording scope admits) |
| `tracklist` | 0 or 1 | a `tracklist` version 1 Claim revision |
| `run` | 0 or 1 | a `derivation_run` version 1 Claim revision |
| `document` | 0 or 1 | an Artifact revision |

The payload is closed. It holds `extent`, may hold `medium` and `track`,
and the asserter fields.

- `extent` is `whole` (the audio is the entire recording and nothing else)
  or `part` (it is only part of the recording: an excerpt, a rip whose fade
  was cut short). Audio that holds more than one recording, such as a
  single-file rip of a whole disc, is narrowed first by an `audio_selection`
  of the recording's samples; that selection is the `audio`.
- `medium` and `track` are given exactly when a `tracklist` is pinned: the
  audio was taken from that track of that tracklist's release, a rip's
  provenance. They are positions (a medium's `position`, a track's
  `position`), the medium is at least 1 and the track at least 0. The
  tracklist must list that position, and the track there must pin the same
  recording object as this claim's `recording` (any revision of it).

```json
{
  "agent": "urn:uuid:…(a person)",
  "payload": {"extent": "whole", "medium": 2, "track": 2},
  "refs": [
    {"role": "recording", "id": "urn:uuid:…(recording 1)", "revision_id": "sha256:…"},
    {"role": "audio", "id": "urn:uuid:…(the ripped file's Artifact)", "revision_id": "sha256:…"},
    {"role": "tracklist", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

A take in the archive still needs no recording Entity (ADR 0078); one is made
when the take is released, or when an outside catalogue lists it. An audio
fingerprint lookup is a machine asserting this claim with its `run` and
`confidence`. Several recording Entities may claim the same audio, and the
same recording may have many claimed files (a CD rip and a vinyl rip); all
are shown, none preferred.

### `archive.claim/catalogue_correspondence` version 1

ADR 0042's mapping claim, for artists, releases, and recordings, and for
both MusicBrainz and Discogs.

| Role | Count | Pins |
|---|---|---|
| `artist` | 0 or 1 | an Entity revision of kind `person` or `organisation` (a band is an organisation, as in ADR 0078) |
| `release` | 0 or 1 | an Entity revision of kind `release` |
| `recording` | 0 or 1 | an Entity revision of kind `recording` |
| `run` | 0 or 1 | a `derivation_run` version 1 Claim revision |
| `document` | 0 or 1 | an Artifact revision |

Exactly one of `artist`, `release`, and `recording` is pinned. The payload
is closed: `external`, `mapping`, and the asserter fields. `external` is a
canonical URI of one of these entries, and the entry decides which subject
it maps from and by which SKOS terms:

| `external` | Subject | `mapping` |
|---|---|---|
| `https://musicbrainz.org/artist/<MBID>` | `artist` | `skos:exactMatch` or `skos:closeMatch` |
| `https://www.discogs.com/artist/<id>` | `artist` | `skos:exactMatch` or `skos:closeMatch` |
| `https://musicbrainz.org/release/<MBID>` | `release` | `skos:exactMatch` or `skos:closeMatch` |
| `https://www.discogs.com/release/<id>` | `release` | `skos:exactMatch` or `skos:closeMatch` |
| `https://musicbrainz.org/release-group/<MBID>` | `release` | `skos:broadMatch` |
| `https://www.discogs.com/master/<id>` | `release` | `skos:broadMatch` |
| `https://musicbrainz.org/recording/<MBID>` | `recording` | `skos:exactMatch` or `skos:closeMatch` |

An MBID is a lowercase UUID; a Discogs `<id>` is the decimal number with no
leading zero and no title slug (`/release/123`, not `/release/123-Title`),
and the host is exactly `www.discogs.com`. Every other URI is refused, so two
claims about the same entry compare as equal strings.

**A group of editions is broader than an edition.** A Discogs master and a
MusicBrainz release group gather the editions of one album. A `release`
Entity is one edition, so it maps to a group only by `skos:broadMatch` (the
external concept is broader), and never by `exactMatch`, whose transitivity
would make every edition of the group one concept. Archive has no Entity
kind for a release group; when one exists, a later version can map it by
`exactMatch`.

**No recording on Discogs, no Work here.** Discogs lists tracks inside a
release and has no recording entry, so a recording maps only to MusicBrainz;
Discogs's view of a recording is the tracklist claim read from its release.
A `musical_work` keeps ADR 0042's `work_musicbrainz_correspondence`; this
contract refuses a Work URI rather than offer a second way to say the same
thing.

```json
{
  "agent": "urn:uuid:…(a person)",
  "payload": {"external": "https://www.discogs.com/master/…", "mapping": "skos:broadMatch"},
  "refs": [{"role": "release", "id": "urn:uuid:…", "revision_id": "sha256:…"}],
  "supersedes": []
}
```

Everything else is ADR 0042's: the claim's identity is opaque, never the
URI; a correction supersedes; a competing mapping stays visible; World may
propose a correspondence but never commits one, and a catalogue search
result is only a candidate until a person asserts it or a saved response is
admitted and pinned. World projects a local SKOS concept for the Entity's
catalogue identity and one for the external entry, relates them by the
stated term with a link to the exact claim revision, and never asserts
`owl:sameAs` or merges Archive object IDs. Two local Entities that map
`exactMatch` to the same entry are a likely duplicate, shown to Review;
nothing merges them.

An importer recognises an Entity it made before by looking up correspondence
claims by `external` (`corresponding` in `world/catalogue.py`). That is a
read; making a new Entity, or reusing one, is still a proposal through Door.

### Credits per track

A release credit applies to every track of the release, as MusicBrainz's
style guide reads a release relationship. World computes, never stores, the
credits that apply to one recording Entity, each with why
(`track_credits` in `world/catalogue.py`):

- a contribution of scope `recording` that pins the recording Entity, or of
  scope `span` or `channel` whose `recording` is it: *via recording*;
- a contribution whose `recording` (or, for a span or channel without one,
  `source`) is audio that a `recording_audio` claim says is this recording:
  *via audio*, naming that claim;
- a contribution of scope `release` about a release whose tracklist places
  the recording: *via release*, naming every tracklist and position that
  placed it. Two tracklists that disagree are both used and both named.

A recording-level credit **refines** a release credit on that track when it
names the same contributor object and the same role type, and its
instruments and vocals are within the release credit's when the release
credit names any, as in ADR 0078. Both stay shown. A release credit whose
role type has no recording counterpart (mastering, liner notes) still
applies to the track as a release credit; it is never refined. This view is
beside ADR 0078's resolved view, which is unchanged: that view still relates
only claims that pin each other's subjects, and still uses no containment
from a release.

### Versioning and what stays valid

All three contracts are new, so no earlier claim changes meaning. ADR 0042's
`work_musicbrainz_correspondence` version 1, ADR 0078's `contribution` and
`band_membership` version 1, and ADR 0048's `artist_credit` version 1 are
unchanged. A later change to any of the three shapes here is a new version,
and version 1 stays admissible.

### Door profile rules a release must add

Door cannot admit these contracts until a specification release installs
their bundles. Beyond the rules spec D-17 already has (Entity-kind targets,
Claim-contract targets, a target that may be an Artifact or a Claim of a
contract, the asserter fields), the release needs:

- index alignment between the `track` references and the tracks of a nested
  `media` list (ADR 0045's alignment, one level deeper), with a total bound;
- strictly ascending positions, and consecutive positions unless `partial`;
- "exactly one of" over three reference roles, and an external-URI pattern
  that decides which role and which mapping terms are allowed;
- for `recording_audio`, a check into the pinned tracklist's payload and
  references: the position exists, and its `track` reference names the same
  object as `recording`. ADR 0077's `entry` check into a pinned sequence is
  the same kind of rule.

## Consequences

- A release's credits reach each of its tracks, attributed to the tracklist
  that placed them, and a track's own credit is shown refining the release's.
  ADR 0078's consequence "release-level credits do not reach each track" no
  longer holds.
- A file ripped from a release, a recording Entity, and the release's
  catalogue entries are joined by claims, so a lead sheet for a recording can
  show the release it is on and the credits that come with it.
- An importer can recognise artists, releases, and recordings it has seen,
  in both catalogues, and a person can correct a wrong match by superseding
  it or affirm one by a judgment (ADR 0081).
- A bandmate can assert their own tracklist or correspondence under their
  name (ADR 0088); disagreements are kept, not resolved.
- Release groups, labels as catalogue entries, release events, and
  barcodes are still not held.
- No invariant changes. INV-7 keeps every list and mapping; INV-8 ranks no
  catalogue over another, nor over a person; INV-11 holds because the
  mappings relate catalogue concepts, never identities.

## Relation to other decisions

- [ADR 0005](0005-claims-review-and-authority.md): every tracklist and
  correspondence is one asserter's claim; disagreement is kept.
- [ADR 0012](0012-inherited-meaning.md): Release and Recording are
  MusicBrainz's kinds; mappings are SKOS; how they map onto the Library
  Reference Model stays open.
- [ADR 0042](0042-work-correspondence.md): the pattern this record extends,
  unchanged for Works.
- [ADR 0045](0045-audio-claim-contracts.md): the index alignment of
  references to payload entries.
- [ADR 0078](0078-contributions.md): the `release` and `recording` kinds, the
  scopes, and the asserter fields; its resolved view is unchanged, and the
  per-track view sits beside it.
- ADR 0081 (judgment version 2) applies to these claims unchanged; ADR 0082's
  named views define no current tracklist or correspondence; ADR 0084's lead
  sheet may name a recording that `recording_audio` ties to a release;
  ADR 0085's rights claims govern a saved catalogue response admitted as a
  document; ADR 0088 lets a bandmate assert these claims.

## Alternatives considered

- **One correspondence contract per entry kind, as ADR 0042 did for Works.**
  Seven near-identical contracts, each needing its own bundle and projection.
  One contract with a closed table keeps the rule in one place, and Works
  stay where they are because their contract is already in use.
- **`skos:exactMatch` from a release to a Discogs master.** Exact matches
  are transitive: every edition mapped to one master would become one
  concept, erasing the edition distinction the `release` kind exists for.
- **A release-group Entity kind now.** It would settle an open question
  (which edition a group credit means) in passing. `broadMatch` keeps the
  link without it.
- **Tracks as a list of recording object IDs in the payload.** Door could not
  check the targets' kinds, and a recording's revision would not be pinned.
  References, aligned by index, are how ADR 0045 already does it.
- **A recording Entity for every take.** Most takes are never released and
  need no catalogue identity; the selection or occurrence already names the
  take (ADR 0078). `recording_audio` links them only when someone needs to.
- **The tracklist as part of the release Entity.** An Entity payload is
  `{kind, label}` (spec D-8) and has no asserter of its own facts; a
  tracklist from a catalogue and one from a sleeve need to stand side by
  side.
- **Applying release credits by extending ADR 0078's resolved view.** That
  view's rule (a finer claim pins the coarser claim's subject) would change
  meaning for claims already made. A separate per-track view adds the
  containment without rereading old claims.
