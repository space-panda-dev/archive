# 0078. A contribution names who did what, in which role, to how much of the music

Status: accepted (by the operator, 2026-10-18)

An archive can say who a selection is billed to: `archive.claim/artist_credit`
version 1 pins one `audio_selection` and an ordered list of person or
organisation Entities, with no role and no scope
([ADR 0048](0048-review-app-write-contracts.md)). It cannot say who wrote a
Work, who mastered a release, who played trombone on a take, or that the
trombone enters at the bridge. Booklets, Discogs, and MusicBrainz say these
things, and so does the operator, who was in the room.

The operator decided the following on 2026-10-18; this record states it as
given.

- **Credits are deep and detailed**: "the trombone on a specific track ...
  really it's on the segment that he's in".
- **A contribution claim** says that a *contributor* (a person or organisation
  Entity) contributed in a *role* (performer on an instrument, vocals,
  composer, lyricist, arranger, producer, co-producer, engineer, recording,
  mixing, or mastering engineer, string arrangement, programming, and so on)
  to a *scope*.
- **Roles are inherited**: use MusicBrainz relationship types and its
  instrument tree (CC0) rather than inventing a vocabulary, and keep the
  credit exactly as written, verbatim, beside the normalised role.
- **Scope runs from coarse to fine**: a Work (writers, lyricist: the
  operator's original works and works written by bands they are in); a
  release or edition (mastering, executive producer, studio, label); a
  recording or take (performers, engineers, mixer); a span of a recording
  ("trombone from 2:31 to 3:05", "this player on keys from the bridge on");
  or a source within a recording (a stem or an input channel: "channel 3 is
  this player's electric piano").
- **Every credit is asserted by someone**: an outside source (a booklet,
  Discogs, MusicBrainz, with its URL, retrieval date, and the document's own
  wording), a person (the operator: "this player played keys on this take"),
  or a machine (an instrument detector). All coexist
  ([ADR 0005](0005-claims-review-and-authority.md)). A finer-scoped credit
  refines, never contradicts, a coarser one; a resolved view prefers the finer
  scope and records why.
- **Also**: band membership over time (organisation, member, instruments,
  from and to), and places such as studios as credited participants if the
  inherited vocabulary supports them.
- **Chords connect**: the credit must connect to ADR 0077's chord-stack
  `part`, so that a per-player stack can say whose part it is.
- **artist_credit**: decide whether this replaces, extends, or sits beside
  `artist_credit` version 1, keep old claims valid, and say how both read
  together.

## Decision

**`archive.claim/contribution` version 1: one contributor, one role, one
scope, one asserter.** One claim is one credit, the way one MusicBrainz
relationship is one credit: a booklet line naming one person on two
instruments is one claim, and a line naming two people is two. One credit per
claim lets a person affirm or reject exactly one credit
(`archive.assessment/judgment`, ADR 0048), lets a finer claim refine exactly
one coarser claim, and keeps each source's wording beside the one credit it
supports. A release has tens of credits per track, not thousands, so this is
not the record count ADR 0066 refused for spans.

Its payload has a `scope`, one of `work`, `release`, `recording`, `span`, or
`channel`, and its references are:

- at most one `contributor`, an Entity revision of kind `person` or
  `organisation` (a band is an organisation, as MusicBrainz's groups are
  artists);
- the scope's subject, below;
- at most one `run`, an `archive.claim/derivation_run` version 1 Claim
  revision, which a machine's claim pins
  ([ADR 0064](0064-derivation-runs.md));
- at most one `document`, the Artifact revision of an outside source's bytes:
  a scan of a booklet, a saved Discogs or MusicBrainz response.

**The scope's subject.**

| Scope | References | What it means |
|---|---|---|
| `work` | one `work`: an Entity of kind `musical_work` | the composition: writers, lyricist, arranger of the Work |
| `release` | one `release`: an Entity of kind `release` | one edition as issued (MusicBrainz Release): mastering, executive producer, label, studio |
| `recording` | one `recording`: an Entity of kind `recording`, an `audio_selection` or `song_occurrence` version 2 Claim (a take in the archive), or an Artifact (a whole file) | one recording or take: performers, engineers, mixer |
| `span` | one `source` Artifact, one `stream` (`audio_stream` version 1 about that Artifact revision), at most one `recording` | sample ranges of a recording: "trombone from 2:31 to 3:05" |
| `channel` | one `source` Artifact, at most one `stream`, at most one `recording` | one source within a recording: a stem, an input channel's own file, or one channel of a multichannel file |

A `span` claim's payload has `sample_rate`, equal to the stream's, and
`spans`: 1 to 1,000 objects of `start_sample` and `end_sample`, ordered,
not overlapping, and ending at or before the stream's `sample_count`, as
[ADR 0066](0066-span-suggestions.md)'s spans are. "From the bridge on" ends at
the take's last sample. Time is integer samples of the pinned stream
([ADRs 0045](0045-audio-claim-contracts.md) and
[0065](0065-audio-stream-facts.md)). A `channel` claim may give `channel`, the
1-based index of one channel of a multichannel source; then it pins the stream
and the index is at most the stream's `channel_count`. A channel's own file,
or a separated stem, needs no index. The optional `recording` reference of a
span or channel says which recording the audio is part of; it is how a
finer claim is placed under a coarser one (below).

`release` and `recording` are two new Entity kinds, after MusicBrainz's
Release and Recording, which [ADR 0012](0012-inherited-meaning.md) already
names as the kinds for music. Their identities are opaque, never a title or
an MBID; how they map onto the Library Reference Model stays an open
question. A take in the archive needs no `recording` Entity: the selection
or occurrence already names it.

**The role is a MusicBrainz relationship type.** The payload's optional
`role` has a `type`, which is the name of a MusicBrainz relationship type for
the entity pair the scope implies: artist-work for `work`; artist-release and
place-release for `release`; artist-recording and place-recording for
`recording` and `span`; artist-recording for `channel`. So `composer` is
refused on a recording, and `mastering`, which MusicBrainz deprecated on
recordings, belongs to a release. It may carry:

- `attributes`: the type's own MusicBrainz attributes, distinct and in
  ascending order (`additional`, `guest`, `solo`, `assistant`, `associate`,
  `co`, `executive`, and for mastering `pre` and `re`). A co-producer is
  `producer` with `co`; an executive producer is `producer` with
  `executive`.
- `instruments`: 1 to 16 distinct MusicBrainz instrument URIs
  (`https://musicbrainz.org/instrument/<MBID>`), for the types that take an
  instrument (`instrument`, `instrument arranger`, `programming`,
  `instrument technician`). The `instrument` type needs one. A string
  arrangement is `instrument arranger` with MusicBrainz's strings.
- `vocals`: MusicBrainz vocal attributes (`lead vocals`, `background
  vocals`, `choir vocals`, voice types), for `vocal` and `vocal arranger`.

The one local role type is `label`, on a release, because MusicBrainz keeps a
release's label as release data, not as a relationship. The table of types,
their MusicBrainz identifiers, and their attributes is in
`world/contributions.py`, read from musicbrainz.org on 2026-10-18; the
specification release ships it as a SKOS scheme whose concepts are each
`skos:exactMatch` the MusicBrainz link type, so a renamed MusicBrainz type
keeps its meaning by identifier.

**The credit as written is kept.** `text` (1 to 1,000 characters) is the
credit exactly as the source printed it, and `credited_as` (1 to 200) is the
contributor's name as printed (MusicBrainz's "credited as"). A claim that
pins a document must keep `text`. A claim may omit `role` and keep only
`text`, when the source's role maps to no MusicBrainz type ("sonic
weather"); nothing guesses a mapping. A claim names a contributor, a role, or
both: a role with no contributor is "someone played trombone here", which is
what an instrument detector can say.

**Places are credited as written until a place can be named.** MusicBrainz
credits studios through place relationship types (`recorded at`, `mixed at`,
`mastered at`, ...). Archive has no Entity kind for a place, and local place
identity is open ([ADR 0043](0043-geographic-evidence-and-route-handoff.md)),
so a place-type role pins no `contributor` and keeps the place as `text`. A
later decision that settles place identity can add a contributor kind without
changing the claims already made.

**The asserter.** As in ADR 0077:

- a machine's claim pins its `run`; its uncertainty is an optional
  `confidence` of `scale` (1 to 200 characters) and `value` as exact decimal
  text, compared only within its own scale (INV-8);
- an outside source's claim pins the `document` and keeps its wording; it may
  give `document_url` (an http or https URL of at most 2,000 characters),
  `retrieved_at` (an RFC 3339 full date or UTC instant), and `locator`
  (1 to 200 characters naming where in the document: a page, a track's
  credit list). The URL and date say where it came from; the pinned Artifact
  is the evidence ([ADR 0042](0042-work-correspondence.md)). A span claim
  from a document also states its `alignment` (`method`, and optionally
  `max_error_samples`), because the document's 2:31 is on its own timeline,
  not the archive's samples;
- a person's claim names the person as its envelope `agent`.

None of these fields is allowed without the document it describes.

**`archive.claim/band_membership` version 1.** MusicBrainz's `member of
band`, over time. It pins one `band` (an organisation Entity) and one
`member` (a person Entity), and at most one `run` and one `document`. Its
payload may have `attributes` (`additional`, `original`, `eponymous`),
`instruments` and `vocals` as above, `from` and `to` as MusicBrainz partial
dates (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`), and `ended` (true only when the
membership ended at an unknown date); the latest day `to` can mean is not
before the earliest day `from` can mean. It keeps the asserter fields of a
contribution. A second stint is a second claim. Membership is not a
contribution: it relates two agents, not an agent to music, so it is its own
contract rather than a scope.

**A finer scope refines, and the resolved view says why.** No claim implies
absence: a span credit says nothing about the samples outside its spans, and
a recording credit does not say the player sounds in every sample. So a finer
claim never contradicts a coarser one; at worst it adds a contributor the
coarser source left out. The resolved view, computed by World and never
stored, relates claims by this rule:

- a claim **refines** another when both name the same contributor object and
  the same role type, the finer one's instruments and vocals are among the
  coarser one's (when the coarser names any), and the finer subject is part
  of the coarser by a reference the claims pin: a span or channel whose
  `recording` reference, or whose `source` Artifact, is the coarser claim's
  recording; a span within a channel of the same source; spans within the
  spans of another claim about the same source;
- a claim with no contributor that matches in role and place **locates** a
  coarser credit without naming anyone: a detector's trombone spans locate
  the booklet's trombonist, and never become that person's claim;
- claims at the same scope never refine one another. Two sources crediting
  the same thing are two claims, both shown.

Each claim in the view lists what it refines, what refines it, what locates
it, and a sentence per relation naming the contributor, role, and reference
that placed it. A claim nothing refines is *finest*; a display shows finest
claims first, with the coarser claims they refine beside them, never hidden.
Asserters play no part in the rule: a person's span may refine a booklet's
recording credit, and a booklet's span may refine the operator's. Release to
recording and Work to recording containment are not used: no contract yet
states which recordings a release carries, and a Work's writers are not its
recordings' performers. This view is not a current rule; neither contract
defines one, and asking which credit is current stays unsupported.

**artist_credit stays beside, unchanged.** `artist_credit` version 1 is
billing: whom a selection is presented as by, in order, like MusicBrainz's
artist credit, which MusicBrainz also keeps apart from its relationships.
`contribution` is participation: who did what. Neither is derived from the
other. Being billed does not mean playing, and playing does not mean being
billed. Every existing `artist_credit` claim stays valid with its meaning.
Read together, a reader shows a take's `artist_credit` as its credit line and
its contributions, grouped by scope, as its credits; a contribution whose
`recording` is that selection is about the same take. Version 1 is not
extended, so no archive needs a second reading of an old claim.

**Whose part a chord stack is.** ADR 0077's `chord_stacks` claim names its
part in free `part` text, and may pin the stem or channel it analysed. Two
joins connect it to a contributor:

- a stack analysed from a player's channel or stem: a `channel` contribution
  about that same Artifact says whose it is, with no change to ADR 0077;
- a stack from a document's track (a transcription's trombone track) has no
  audio of its own: this record amends ADR 0077 so that `chord_stacks`
  version 1 may pin at most one `contribution`, an
  `archive.claim/contribution` version 1 Claim revision whose subject is the
  stack's recording, before either contract is released.

`part_contributions` in `world/contributions.py` gives both joins, with why.

**The rules live in World until a release installs them.**
`world/contributions.py` holds every rule above as World will re-check it,
the inherited role table, the resolved view, and the part join. It uses only
the standard library, so an importer can check a proposal before Door does.
Door cannot admit either contract until a specification release installs
their bundles with the two new Entity kinds; the release adds the Door
profile rules this record needs: references whose required roles depend on a
payload value (`scope`), a target that may be an Entity of a kind, a Claim of
a contract, or an Artifact, a role vocabulary per scope, the existing
sequential-span and same-subject rules, and a partial-date order. How the
first importers assert is in [contribution sources](../contribution-sources.md).

**New nouns.** Contribution, contributor, scope, role, credit text, refines,
locates, finest, and band membership enter the
[vocabulary](../vocabulary.md). None is a subsystem: each names part of a
claim, or a relation in a view of claims. They own no verb beyond
Annotation's and Enrichment's *assert* and *derive*, enforce no new
invariant, and hold no state outside the claims Door admits.

## Consequences

- A booklet, Discogs, MusicBrainz, the operator, and a detector can each
  credit the same trombone, each attributed, and none is lost when another is
  added. MusicBrainz and Discogs often copy a booklet, so agreement between
  them is not independent evidence; the view counts nothing.
- The operator can place a player at the samples they play, refining a
  booklet's whole-track credit, and the view shows both.
- A multitrack capture's channels can be credited once, and every claim
  analysed from a channel (a chord stack, a transcription) inherits whose it
  is through the Artifact.
- Studio credits are typed but nameless until place identity is decided.
- Release-level credits do not reach each track until a tracklist contract
  exists.
- No invariant changes. INV-7 keeps every credit; INV-8 keeps confidence in
  its own scale and ranks no asserter.

## Relation to other decisions

- [ADR 0005](0005-claims-review-and-authority.md): every source's credit is a
  claim; disagreement is kept, and judgment is an assessment of one credit.
- [ADR 0012](0012-inherited-meaning.md): roles, attributes, instruments, and
  vocals are MusicBrainz's; the RDF shape is PROV's qualified attribution (a
  contribution maps to a `prov:Attribution` whose `prov:hadRole` is the
  MusicBrainz type), used as a mapping only (INV-11).
- [ADR 0042](0042-work-correspondence.md): a URL is never the evidence; the
  document is admitted first. Matching an Entity to a MusicBrainz artist,
  release, or recording needs correspondence claims like the Work's.
- [ADR 0048](0048-review-app-write-contracts.md): `artist_credit` stays as
  billing; judgments apply to contributions unchanged.
- [ADR 0077](0077-chord-stacks-and-meter.md): the same reference
  pattern (`source`, `stream`, `run`, `document`, alignment), and the
  `contribution` reference above.

## Alternatives considered

- **Extend `artist_credit` with roles and scopes.** Its claims are billing
  for one selection; adding roles would give old claims a second reading
  (was an unroled artist a performer?), and a selection is only one of five
  scopes.
- **Free-text roles.** Nothing could be compared or queried ("Bass",
  "bass guitar", "B. Gtr"); the text is kept anyway, beside the type.
- **MusicBrainz only.** Importing relationships as MusicBrainz states them
  would hold neither a booklet nobody entered there, nor the operator's own
  knowledge, nor spans and channels, which MusicBrainz cannot express.
- **Music Ontology or PROV roles as the vocabulary.** The Music Ontology's
  performance events can express parts of recordings, but it is no longer
  maintained and its instruments come from MusicBrainz anyway. PROV gives the
  structure (an attribution with a role) but no roles; it is the mapping, not
  the vocabulary.
- **One claim per source document.** Judging one wrong credit would judge a
  whole booklet, and refinement would relate lists rather than credits.
- **A finer credit superseding a coarser one.** Supersession is for the same
  asserter correcting a claim; a span from the operator does not withdraw a
  booklet's credit, and storing it as one would erase the booklet's view.
