# Contribution sources

How the first importers would turn a booklet, Discogs credits, and
MusicBrainz relationships into `archive.claim/contribution` and
`archive.claim/band_membership` claims
([ADR 0078](decisions/0078-contributions.md)).

**Future work.** None of these importers is built, and no archive holds a
claim from one. This is a design for code not yet written. Specification
0.0.9 installs the contracts (D-17), so Door would admit what an importer
proposed; until one exists, a contribution is made only by hand. Every
example is invented.

## What every importer does

1. **Admit the document first.** The evidence is the bytes the importer read:
   a scan or photograph of a booklet, a saved Discogs API response, a saved
   MusicBrainz web-service response. Each is admitted as an Artifact before
   any claim pins it ([ADR 0042](decisions/0042-work-correspondence.md)). A
   URL and retrieval date are kept in the claim as where the bytes came from,
   never instead of them.
2. **Resolve each name to an Entity, or stop.** A contributor is a person or
   organisation Entity the operator already has, or one the importer proposes
   in the same batch with a label taken from the source. A name it cannot
   resolve without guessing (two people with one name, a pseudonym) is
   reported, and its credit is not proposed. Matching an Entity to a
   MusicBrainz or Discogs artist needs a correspondence claim like the Work's,
   which no contract yet provides; until one does, matching is the operator's
   decision.
3. **Resolve the subject by the rules below, or stop.** Nothing infers a Work
   from a title.
4. **Map the role, or keep only the text.** The source's role becomes a
   MusicBrainz type with attributes, instruments, and vocals where the mapping
   tables below say so. A role they do not cover is proposed with no `role`
   and its `text`, never forced into the nearest type.
5. **Keep the source's own words.** `text` is the credit line as printed or
   as the source returned it; `credited_as` is the contributor's name as
   printed; `locator` says where in the document.
6. **Check before proposing.** `world/contributions.py` checks every claim;
   Door checks it again on admission.
7. **Stay quiet.** A rerun on the same document proposes nothing. Each claim's
   identity name is `contribution-` followed by the lowercase SHA-256 of the
   canonical JSON of the document's revision ID, the locator, the text, the
   contributor's object ID, and the subject's object ID. The same credit from
   the same document is therefore the same object, and a changed document is a
   new revision of the Artifact and so a new claim. A person's claim, which
   has no document, takes `contribution-` and a random UUIDv4 once.

## Which subject a credit is about

Sources attach credits to a release, a track, a recording, or a work, and the
words do not line up. These rules decide the scope.

| The source attaches it to | Scope and subject |
|---|---|
| a Work (MusicBrainz work relationships; "words and music by" for one song) | `work`: the song's `musical_work` Entity |
| a whole release (MusicBrainz release relationships; Discogs release credits with no track list; a booklet's general credits) | `release`: the release Entity |
| a track (Discogs per-track credits; a Discogs release credit listing tracks; a booklet's per-track credits) | `recording`: the recording that track carries, one claim per track |
| a recording (MusicBrainz recording relationships) | `recording`: the recording Entity |
| a time within a track ("trombone solo 2:31 to 3:05") | `span`, once the track's audio is an Artifact in the archive, with the document's alignment |
| a rehearsal take, by the operator | `recording` on the take's selection or occurrence, or `span` or `channel` on its audio |

Further rules:

- **A track is not a subject.** A track is a recording's place on a release.
  A credit a source gives a track is about the recording on it, so a remaster
  or an edit that MusicBrainz models as another recording gets its own claims.
- **Writers belong to the Work.** Discogs gives "Written-By", "Music By", and
  "Lyrics By" per track. The importer proposes them at `work` scope only once
  the track's Work is known: an Entity the operator named, reached through an
  accepted `performance_work` link or the operator's choice. Until then the
  credit is reported as waiting for its Work and nothing is proposed;
  proposing it on the recording would need a role MusicBrainz does not have
  there.
- **Mastering belongs to the release.** MusicBrainz deprecated mastering on
  recordings. A source that credits mastering on one track is proposed at
  `release` scope with a `locator` naming the track and its text kept; the
  per-track detail survives in the text.
- **A release-wide performer stays on the release.** "Ann Example: trombone"
  in general credits is a release credit. The importer does not copy it onto
  every recording: no contract yet says which recordings a release carries,
  and copying would create claims the source never made.
- **Release groups are not imported.** MusicBrainz release-group credits
  span editions; which edition they mean is undecided.
- **Places are kept as text.** "Recorded at Example Sound Rooms" becomes
  `recorded at` with that text and no contributor
  ([ADR 0043](decisions/0043-geographic-evidence-and-route-handoff.md) leaves
  place identity open).

## Booklets

A booklet is a physical item; its scan or photograph is the Artifact. The
credits are read from the image by a person or by OCR:

- **Read by a person.** The operator types the lines; each claim's `agent` is
  the operator and its `document` the scan. `locator` is the page and line
  ("page 3, line 7").
- **Read by OCR.** The OCR is a derivation run, so each claim pins the run as
  well as the scan, and its `text` is what the OCR read, errors and all. A
  person who corrects a line makes a new claim with the corrected text; the
  OCR's claim stays and can be judged `rejected`.

Booklet roles are free prose. The importer offers the mapping tables below
and the operator confirms or leaves the role unmapped. Lines that name
several people produce one claim each, all with the same `text`. A credit like
"all songs written by The Examples" is one `work` claim per song whose Work is
known, each keeping the whole line.

## Discogs

The saved response is `GET https://api.discogs.com/releases/<id>`, kept as
returned. `document_url` is that URL and `retrieved_at` the time of the
request. Credits are in the release's `extraartists` and each track's
`extraartists`:

- `role` is the source's role text, which may carry details in brackets:
  "Arranged By [Strings]", "Trombone [Solo]". It is kept in `text` with the
  artist's name.
- `anv`, the name as credited, becomes `credited_as`; `name` is used only to
  resolve the Entity.
- `tracks` on a release credit ("A1 to A3, B2") expands to one claim per
  listed track; an empty value means the whole release.
- `locator` is the JSON path of the credit, such as
  `tracklist[1].extraartists[0]`.

Discogs role text to MusicBrainz type, for the common cases (others stay
unmapped):

| Discogs role | MusicBrainz type | Attributes, instruments, vocals |
|---|---|---|
| an instrument name ("Trombone", "Electric Piano") | `instrument` | the MusicBrainz instrument of that name; `[Solo]` adds `solo` |
| "Vocals", "Lead Vocals", "Backing Vocals" | `vocal` | `lead vocals`, `background vocals` |
| "Written-By", "Songwriter" | `writer` | |
| "Music By", "Composed By" | `composer` | |
| "Lyrics By" | `lyricist` | |
| "Arranged By" | `arranger` | "[Strings]" makes it `instrument arranger` with strings |
| "Producer", "Co-producer", "Executive-Producer" | `producer` | none, `co`, `executive` |
| "Engineer", "Assistant Engineer" | `engineer` | none, `assistant` |
| "Recorded By" | `recording` | |
| "Mixed By" | `mix` | |
| "Mastered By" | `mastering` (release scope) | |
| "Programmed By" | `programming` | instrument when bracketed |
| "Remix" | `remixer` | |
| "Featuring" | `performer` | `guest` |

## MusicBrainz

The saved response is a web-service lookup with relationships included, for
example `GET https://musicbrainz.org/ws/2/recording/<MBID>?inc=artist-rels+place-rels+work-rels&fmt=json`.
`document_url` is that URL and `retrieved_at` the time of the request.
MusicBrainz's types are already the vocabulary, so mapping is exact:

- `relations[].type` is the role type, and `attributes` splits into
  `instruments` (instrument names resolved to their MBIDs through the
  instrument the relationship links), `vocals`, and the type's other
  attributes.
- `target-credit` becomes `credited_as`, and `attribute-credits` (an
  instrument credited as "bones") are kept in `text`, which the importer
  writes as the relationship's phrase with the credited names, since
  MusicBrainz has no printed line.
- `locator` is the JSON path of the relationship, such as `relations[4]`.
- The subject is the entity looked up: a release lookup gives `release`
  claims, a recording lookup `recording` claims, a work lookup `work` claims.
  Artist relationships of type `member of band`, with their `begin`, `end`,
  and `ended`, become `band_membership` claims.
- Relationship `begin` and `end` dates on a recording (when a session took
  place) are not part of a contribution and are not imported.

## One claim per source, never merged

The same credit from three sources is three claims, each with its own
document, agent, and wording. The importer never skips a credit because
another source already made it, never merges two sources into one claim, and
never supersedes another source's claim. Supersession is for one asserter
correcting itself: a Discogs response saved again after the credit changed
is a new Artifact revision and a new claim, and the earlier claim stays,
because what Discogs said then is still evidence.

Agreement is not independence: MusicBrainz and Discogs are often entered from
the same booklet. The resolved view shows each claim with its source and
counts nothing.
