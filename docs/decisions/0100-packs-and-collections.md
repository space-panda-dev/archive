# 0100. Sample packs are releases, libraries are collections, and source paths are kept

Status: proposed (wave 2, drafted 2026-10-19)

The sample app browses three kinds of organised audio beside the cuts made
in the review app: commercial sample packs, personal libraries (the
operator's own folders, a friend's crate), and the banks of a hardware
sampler. Each is admitted and backed up, and each arrives as a folder of
files whose names and folders are most of what anyone knows about them
("Drums/Kicks/808_hard.wav"). Nothing in the archive can hold any of that.

- **An Artifact is only bytes.** Its payload is its content digest and size
  (D-8), and the same kick in two packs is one Artifact. Which pack, library,
  or bank a file belongs to cannot be said, and neither can its name.
- **The one filename field is a document's.** `document_source`
  ([ADR 0085](0085-documents-lyrics-rights.md)) may give a `filename`,
  "never a path", and describes documents only. Audio has no name at all.
- **A release lists recordings, not files.** `release` is one edition as
  issued and `archive.claim/tracklist` ([ADR 0087](0087-tracklists-and-correspondence.md))
  lists its tracks, each a `recording` Entity, at most 4,000. A pack of
  6,000 one-shots is neither 6,000 recordings nor within that limit.
- **The only membership contract is a session's.** `session_parts`
  ([ADR 0048](0048-review-app-write-contracts.md)) lists a session's files
  in recording order, and `capture import` ([ADR 0070](0070-capture-import.md))
  reads only recorder cards, through recorder grammars (`capture/grammar.py`).
- **Origin and rights are ready for packs.** [ADR 0096](0096-origin-of-recordings.md)'s
  origin has `sample_pack` (naming the publisher, optionally the `release`,
  with a `depositor` for a friend's library), and [ADR 0097](0097-rights-by-use.md)
  expects "the sample-pack importer (wave 2)" to propose a version 2 rights
  claim from the pack's licence (typically derive yes, redistribute no).

The operator decided the following on 2026-10-19 (wave 2 decisions W2.3 and
W2.4, with the earlier settled decision on sampler contents); this record
states it as given.

- **W2.3 Packs and collections.** "A commercial sample pack is a `release`
  (an edition as issued, with its publisher). A personal library, a friend's
  crate or a sampler bank is a new Entity kind, `collection`, whose members
  are given by a membership claim. `collection` is the one new noun."
- **W2.4 Source paths.** "The original path and filename
  ("Drums/Kicks/808_hard.wav") are kept as a source-path claim (like
  `document_source`); a machine proposer turns paths into form and sound
  claims beside people's."
- **Sampler contents.** "Sampler contents (SP-404-style SD card banks,
  OP-1-style exports) import as libraries in wave 2; performance lineage
  (which pad fired which sample when) waits for a later wave."

Form and sound are the classification claims of [ADR 0099](0099-samples.md) (proposed, wave 2)
(W2.1); this record proposes them and defines neither.

## Decision

### The `collection` Entity kind

A specification release adds the Entity kind **`collection`**: a set of
audio someone gathered, kept as a set. Its payload is the closed
`{kind, label?}` of every Entity (D-8); `label` is its name ("Rosa's crate",
"Bank card 2"), and its identity is opaque, never a name or a path, as
`release` and `recording` are. It maps to `arch:Collection`, with
`prov:Collection` as a mapping only ([ADR 0012](0012-inherited-meaning.md)).

A collection is the operator's to create, like every Entity
([ADR 0094](0094-peoples-writes.md): members admit Artifacts, not Entities).
Whether it is a library, a crate, or a sampler bank is not a field: the
depositor on its members' origin claims, its members' slots, and its label
say so. Retiring a collection Entity retires the set, never its members.

A **commercial sample pack is a `release`** Entity, unchanged: its label is
the pack's name, its publisher is an ADR 0078 contribution at release scope
with the local `label` role type, and a new version of a pack that adds or
changes files is another release, by MusicBrainz's own rule (ADR 0087).
Files shared between two editions are one Artifact, so a second edition
admits only what is new.

### `archive.claim/collection_members` version 1

A **membership claim** says which items are in a collection, or which files
a sample-pack release carries. It is one contract for both containers, so
the sample app reads one shape (see Open for the operator, 1).

| Role | Count | Pins |
|---|---|---|
| `collection` | exactly 1 | an Entity revision of kind `collection` or `release` |
| `member` | 1 to 1,000, distinct objects | an Artifact revision, or an `archive.claim/audio_selection` Claim revision (version 2, or ADR 0099's version 3 once installed); only Artifacts when the container is a `release` |
| `run` | 0 or 1 | a `derivation_run` version 1 Claim revision, when an importer made it |
| `document` | 0 or 1 | an Artifact: a pack's manifest, a sampler's bank file |

Its payload is closed, every field optional:

```json
{
  "slots": [{"slot": "A-01"}, {"slot": "A-02"}, {}],
  "note": "card 2 as of the spring set"
}
```

- `slots`, when present, has exactly one entry per `member` reference, in
  order: `{}`, or `{"slot": <1 to 32 characters>}`, the position a device
  or the asserter names ("A-01", "pad 7"). No two members of one claim share
  a slot. Members otherwise have no order.
- `note`: free text, 1 to 2,000 characters.

**Members are the union of the head claims.** A collection's members are
the members of every head membership claim whose `collection` is that
object, except a claim the active operator has judged `rejected`
([ADR 0081](0081-judgments-and-annotations.md)). A large library is many
claims of up to 1,000 members each; removing a member is a revision of the
claim that listed it, without it, and the earlier revision stays as history
(INV-1, INV-7). Re-importing a changed sampler card revises the claims to
match the card, so a bank's history is its claims' revisions.

**Door rules** (a specification release adds them with the bundle): the
container kind; distinct members of the allowed shapes, Artifacts only
under a `release`; `slots` the same length as `member`, slots distinct.
The agent is the operator, or the importer's process with a `run`.

**Membership never changes reach.** A membership claim pins its container
and members, so the claim itself reaches no further than its strictest
member, but World's pin map gains no edge from a member to its container:
being in the operator's favourites never narrows a sample's reach, and
being in a pack never widens it. What a pack's licence permits reaches its
files through rights claims naming them (below).

### `archive.claim/source_path` version 1

A **source-path claim** says where files were found when they were taken
in: their path relative to the folder that was imported. It is history,
never a location (INV-6): moving, copying, or dropping a copy changes
nothing, and a path never identifies anything
([ADR 0027](0027-core-payloads-and-installed-contracts.md)).

| Role | Count | Pins |
|---|---|---|
| `file` | 1 to 1,000 | an Artifact revision, one per entry, in payload order; the same object may repeat (one kick at two paths) |
| `within` | 0 or 1 | an Entity revision of kind `collection` or `release`: the set the imported folder was |
| `run` | 0 or 1 | a `derivation_run` version 1 Claim revision, when an importer read the paths |

```json
{
  "root": "Harbor Light Drum Kit v2",
  "observed_at": "2026-10-19T10:04:00Z",
  "entries": [
    {"path": "Drums/Kicks/808_hard.wav", "modified": "2024-03-02T11:20:00Z"},
    {"path": "Drums/Snares/snare_tight_02.wav"},
    {"path": "Loops/92/break_dusty_92bpm.wav"}
  ]
}
```

- `entries` (required) has exactly one entry per `file` reference, in
  order. `path` is 1 to 1,024 characters of Unicode in NFC, segments joined
  by `/`, with no leading `/`, no empty, `.`, or `..` segment, no `\`, and
  no control character; its last segment is the file name. It is relative
  to the imported folder, so it never holds the importer's home directory,
  mount point, or volume. A path read from a Windows card has its
  separators turned to `/`. `modified` (optional) is the file's
  modification instant as the file system stated it, an RFC 3339 UTC
  instant.
- `root` (optional, 1 to 255 characters, a single segment) is the imported
  folder's own name or the card's volume label.
- `observed_at` (required) is when the paths were read, an RFC 3339 full
  date or UTC instant: a path is true of a moment, so a later import of a
  reorganised folder is a new claim beside the old one.

**Door rules**: `entries` the same length as `file`; every path well formed
and distinct within the claim; `within` of an allowed kind. The agent is
the operator, a member under `admit` (below), or the importer's process
with a `run`.

`document_source`'s `filename` stays as it is; a document taken from a
folder may also have a source path.

### The library importer

`capture library` is a second importer beside `capture import`, sharing its
rules (ADR 0070): it hashes every file and skips content already admitted,
never changes its input, and runs against the shadow or the real archive
only with the operator's approval. It walks one folder and admits, in
batches of at most 1,000 files (ADR 0023):

1. an Artifact per new audio file (the media types members may stage under
   ADR 0094, nothing else; other files are reported and left);
2. one membership claim per batch into the container the operator names:
   an existing `collection` or `release` by its ID, or a new one with a
   label given on the command line;
3. one source-path claim per batch, `within` that container;
4. an origin claim per batch (ADR 0096) as the operator states it on the
   command line: `sample_pack` with the publisher and `release` for a pack;
   for a friend's crate, the value the friend gives (`unknown` when they
   cannot say) with the friend as `depositor`; for the operator's own
   folders or a sampler card, `unknown` unless the operator says otherwise.
   Nothing infers origin from a path;
5. for a pack, a rights claim version 2 (ADR 0097) built from the licence
   the operator points to, with the licence admitted as its `document`,
   the publisher as `holder`, and as `subject` the release and up to 999 of
   its files per claim, so every file carries the licence it reaches by
   (Open for the operator, 3). Rights stay the operator's to admit.

Re-importing the same folder changes nothing; importing a changed folder
adds new Artifacts and revises the membership claims. Each new Artifact
then runs the enrichment chain as `capture import`'s do, which in wave 2
includes the bank features ADR 0099's views read and the path proposer
below.

**Sampler banks use bank grammars.** As a recorder grammar names one
recorder model, a **bank grammar** names one sampler's card or export
layout: which folders hold samples and how a file name or folder gives a
slot. It reads names, sizes, and headers only. A device whose pad
assignments live only in a proprietary project file gets no slots in wave 2;
that file is admitted whole as an Artifact pinned as the membership claim's
`document`, as [ADR 0101](0101-daw-projects.md) (proposed, wave 2) admits a DAW snapshot, and
is not parsed. Which pad fired which sample when is the later wave's.

### The path proposer (W2.4)

A machine proposer reads source-path claims and proposes ADR 0099's form
and sound claims about each file, as a derivation run
([ADR 0064](0064-derivation-runs.md)) asserting through its process agent:

- It matches the path's segments against a keyword table kept as data
  ("kick", "bd", "kik" to the MusicBrainz instrument for bass drum; "loop",
  "one shot", "oneshot", "fx", "break" to the form scheme's concepts). The
  table's digest and version are run parameters, so a run is reproducible.
- A folder word counts for every file under it; a file name's word wins
  over its folder's when they disagree. A path with no match yields no
  claim, never `unknown`.
- Each claim pins the source-path claim it read and gives a confidence on
  the run's own scale saying which segment matched. People's claims stand
  beside it (INV-7); which wins is ADR 0099's views' rule, never this run's.

It proposes no free tags and no origin, and it reads no audio.

### World, the relay, and abilities

- **Projection.** A `collection` Entity projects as `arch:Collection`; a
  membership claim as `prov:hadMember` from container to each member, with
  the slot on a resource of its own; a source-path claim as one resource per
  entry under `arch:` terms (`arch:source_path`, `arch:source_root`,
  `arch:observed_at`). A rejected membership claim is projected with its
  judgment, never dropped.
- **Views.** `collection_members` answers a collection's or release's
  members, with slots, each member's source paths and origin, and each
  claim's standing. `source_paths` answers an Artifact's paths across every
  import. `folder_tree` answers a container's members arranged by their
  source paths in it, which is the browser the sample app shows. All three
  are regenerable (INV-4).
- **Relay.** The relay serves these views filtered by the caller's readable
  set (ADR 0093), entry by entry: a member it cannot read is left out with
  a withheld count (INV-24), and its path is withheld with it, because a
  path can say what a hidden file is. A raw membership or source-path claim
  is served only when the caller can read every pin, as any claim is.
- **Abilities (ADR 0094).** Under `admit`, a member may describe their own
  staged Artifacts with a source-path claim in the same batch, beside the
  document source and origin claims ADR 0094 already lists; this extends
  that table. Membership claims, collections, releases, and rights stay
  the operator's in wave 2 (Open for the operator, 2). `annotate` covers
  ADR 0099's form, sound, and tag claims about readable members, as that
  record says; `cut` is unchanged.

### New nouns

- **Collection** (Entity kind). (1) It names a set of audio as a person
  gathered it, which neither a release (an edition as issued) nor a session
  (one recorded moment) is. (2) It owns no verb: admitted and retired like
  any Entity, its members asserted by claims. (3) It enforces none itself;
  its members keep INV-24 when served. (4) It cannot be a `release`: a
  friend's crate was never issued, and calling it one would give it a
  publisher and editions it lacks. It cannot be a tag: a tag is a person's
  word about one file and is never a set someone keeps. (5) The Entity and
  its claims are records Door admits; World owns nothing. (6) A detail: an
  Entity kind with one claim contract. The word already means the archive's
  holdings ("take an object out of the collection", ADR 0013); see Open for
  the operator, 9.
- **Membership claim.** (1) It says which items are in a collection or a
  pack, which nothing says of files. (2) No verb. (3) None. (4) It is a
  claim contract. (5) No. (6) A detail. It is distinct from ADR 0078's band
  membership, which relates a person to a band.
- **Source-path claim.** (1) It keeps the path a file was found at, which
  no Artifact field or document source keeps. (2) No verb. (3) It keeps
  INV-6 by being history, not location. (4) It is a claim contract.
  (5) No. (6) A detail.
- **Slot** (a member's position on a device). (1) It records a sampler's pad
  or bank position. (2) No verb. (3) None. (4) A field of the membership
  claim. (5) No. (6) A detail.
- **Bank grammar.** (1) It reads one sampler's card layout. (2) No verb.
  (3) None. (4) It is code in `capture/`, as recorder grammars are.
  (5) No. (6) A library module, not a subsystem.
- **Path proposer** is a derivation run and **library importer** an
  importer; neither is a new noun.

## Consequences

- A pack, a friend's crate, and a sampler card each arrive with their names
  and folders kept, browsable by folder, and classified from those names
  before anyone listens.
- The same file in two packs is one Artifact with two memberships, two
  source paths, and two origin claims; the sample app shows both, and the
  strictest rights apply (ADR 0097).
- A pack's files reach as far as rights claims naming them allow; a file
  missing from those claims reaches service only (INV-21), which is never
  wider.
- Membership is organisation only: adding a sample to a collection changes
  nobody's view of anything else about it.
- A large library is many records: 10,000 files are about ten membership,
  ten source-path, ten origin, and, for a pack, ten rights claims, beside
  the Artifacts.
- No invariant changes, and no accepted or wave-1 ADR is amended except
  ADR 0094's `admit` table, which gains the source-path claim.

## Alternatives considered

- **A pack's files in its tracklist.** Each file would need a `recording`
  Entity and a `recording_audio` claim, three records per one-shot, and
  ADR 0087 caps a tracklist at 4,000 tracks.
- **A pack as a collection.** W2.3 makes it a release: it is issued, by a
  publisher, in editions.
- **The path in `document_source`'s `filename`.** That contract describes
  documents, says "never a path", and carries a media type audio does not
  need here.
- **One source-path claim per file.** Faithful, but ten thousand claims
  for one library; an aligned list as in a tracklist keeps the import small
  and still gives each file its own entry.
- **Membership narrowing reach, so a pack's licence binds its files
  through the release.** It would also let an unrelated collection's
  sensitivity narrow every file put in it, and a rights claim on the
  release alone still leaves each file at service under ADR 0085's rule.
- **Nested collections** (a card holding banks). Source paths and slots
  already give the structure; nesting adds cycles for no reader yet.
- **Parsing sampler project files.** Proprietary and versioned; the whole
  file kept as an Artifact keeps what parsing would later need.

## Touch points

- New `world/collections.py`: `check_collection_members`,
  `check_source_path` (path well-formedness), `members` (the union and the
  operator's rejections), `folder_tree`.
- `world/judgments.py`: `standing`, read for the operator's rejection.
- `world/circle.py`: `MEMBER_CONTRACTS` and `check_member_draft` (a
  source-path claim under `admit`, describing only staged Artifacts in the
  same batch).
- `world/documents.py`: `check_document_admission` accepts a source-path
  claim as a describing claim; `_pins` is unchanged (no membership edge).
- `world/contract_graphs.py`, `world/projection/6/vocabulary.ttl`,
  `world/projection/6/domain.ttl`, `world/projection/6/records.ttl`:
  `arch:Collection`, `prov:hadMember`, and the source-path terms.
- `world/record_views.py` and `world/views.py`: `VIEWS` gains
  `collection_members`, `source_paths`, and `folder_tree`.
- `relay/access.py` and `relay/served.py`: per-entry filtering with withheld
  counts; `check_contents` for a member's source-path claim.
- `capture/capture.py`: `main` gains the `library` command; new
  `capture/library.py` (walk, batch, plan) and `capture/bank_grammar.py`
  (bank grammars, beside `capture/grammar.py`).
- `enrich/proposers.py`: a path proposer, with its keyword table as a new
  data file.
- `door/src/Archive/Door/Types.hs` (the Entity kinds per specification,
  beside `release` and `recording`) and `door/src/Archive/Door/Contracts.hs`
  (the context key `collection` to `arch:Collection`).
- `spec/archive-spec.md`: the Entity kinds paragraph (D-8) and the two
  contract bundles with their profile rules; `docs/vocabulary.md`:
  *collection*, *membership claim*, *source-path claim*, *slot*, *bank
  grammar*, and a qualified sense of "the collection".

## Open for the operator

1. **How a pack lists its files.** W2.3 says a pack is a release but not
   which claim lists a release's files; a tracklist pins recordings.
   Recommended: the membership claim with a `release` container, Artifacts
   only, as above.
2. **Membership through the relay.** Can a friend with `admit` add their
   own uploads to their crate? Recommended: not in wave 2; membership stays
   the operator's until a rule says whose collection a member may add to.
3. **Which subjects a pack's rights claim names.** ADR 0097 says "the pack
   as `subject`"; under ADR 0085's rule a rights claim on the release alone
   leaves every file at service. Recommended: the release and every file,
   up to 999 files per claim, as the importer above writes; the consistency
   pass should align ADR 0097's wording.
4. **Membership and reach.** Should a member inherit its container's limits?
   Recommended: no, as decided above; rights and sensitivity are claimed per
   file.
5. **Tempo and key written in file names** ("break_92bpm_Amin.wav"). They
   are the publisher's statement, not a measurement (W2.2 covers machine
   measurement and people's statements). Recommended: the path proposer
   proposes them as ADR 0077 meter and ADR 0080 key claims pinning the
   source-path claim, judged like any machine claim.
6. **Machine tags from unmatched path words.** Recommended: no; tags are
   people's, and machine tags would flood the unranked list W2.1 keeps.
7. **Sampler project files.** Recommended: admitted whole and unparsed as
   the membership claim's `document`, reusing ADR 0101's packed-file rule.
8. **Path spelling.** Recommended: NFC, case kept as read, `/` separators;
   no other normalisation, so two paths differing only in case stay two.
9. **The word `collection`.** It already names the archive's holdings
   (ADR 0013, *retire*). Recommended: keep W2.3's word and have the
   vocabulary say "the archive's holdings" for the old sense.
10. **Members beyond audio.** The sample app shows songbook songs too.
    Recommended: members are Artifacts and selections only in version 1; a
    Work in a collection waits for a reader that needs it. ADR 0105
    (proposed) makes each notebook a `collection` of text pages, tied to it
    by source-path claims' `within` rather than membership claims, which
    widens "a set of audio" above; the two drafts do not agree and neither
    is chosen here.
