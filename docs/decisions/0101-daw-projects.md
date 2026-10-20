# 0101. A DAW project is an Entity with snapshots, its audio, and lineage to its bounces

Status: accepted (operator, 2026-10-19); proposed in wave 2 and its open
questions settled the same day

A song is often finished in a digital audio workstation (DAW): a project
folder holding the DAW's own project file, the takes recorded into it, the
samples dragged into it, and, somewhere, the bounces exported from it. The
archive can hold each of those files as an Artifact, but it cannot say they
belong together, what state the project was in when a bounce was made, or
how to open that state again.

- An Artifact's payload is only its content digest and size (D-8,
  [ADR 0022](0022-normative-record-envelope.md)). A folder has no digest,
  and the project file alone cannot be reopened without the audio it
  points to.
- The only packed evidence today is [ADR 0036](0036-v2-frozen-migration-bundle.md)'s
  frozen V2 bundle: one deterministic PAX tar with a canonical manifest of
  member paths, sizes, and digests, admitted as one Artifact whose members
  stay individually retrievable. It is one fixed object, for one import.
- No Entity kind names a piece of work that changes over months and is
  saved many times. A `recording_session` is one occasion; a `musical_work`
  is the composition, not a production of it (ADR 0010).
- [ADR 0098](0098-content-lineage.md) lets a bounce say what it was made
  from, but an input must be an Artifact or a selection; a project is
  neither, and a lineage claim has at most 64 inputs, fewer than the files
  of a busy project.
- What reach and destroy follow is the pin map (`_pins` in
  `world/documents.py`, `Archive.Door.Reach`) and, after ADR 0098, lineage.
  A bounce of a project that uses a personal-use sample would otherwise
  reach as far as the bounce's own claims allow.

The operator decided the following on 2026-10-19 (wave 2 decision W2.5,
and the lineage decisions of wave 1, W1.12 to W1.14, which ADR 0098 states);
this record states it as given.

- **W2.5 DAW projects.** A project is an Entity. Each meaningful snapshot,
  meaning one the operator asks for with a snapshot command rather than
  every save, is admitted whole as one packed file, so it can be reopened.
  Its audio files are also admitted individually; deduplication makes this
  nearly free. Bounces link by lineage (ADR 0098). Wave 2 does not parse
  the DAW's project format.

The operator settled this record's open questions on 2026-10-19 (round 6
answers R6.7, R6.8 and R6.17; R6.16 settles the rest as recommended). The
decision below states the settled choices; the list at the end records them.

## Decision

### The Entity kind `daw_project`

A **DAW project** is an Entity of a new kind, `daw_project`: one
production in one DAW, across all its saves. Like `recording_session` it is
a local kind with no Library Reference Model equivalent (P-14), mapped to
`arch:DawProject`. Its `label` is the project's name, which never supplies
identity; its identity is opaque, allocated by the snapshot command on the
project's first snapshot (ADRs 0021 and 0026), never derived from a folder
path or a file name. A specification release adds the kind to the core
context additively (D-8), as 0.0.9 added `release` and `recording`. It is
wave 2's second new Entity kind beside `collection`; W2.3's "the one new
noun" spoke of packs and collections (R6.7).

### The snapshot pack

A **snapshot pack** is the packed file W2.5 asks for: the project folder
as one Artifact, from which the folder can be rebuilt. It is **slim**
(R6.8): every member the snapshot admits individually (its audio and MIDI,
below) is listed in the manifest by path, size, and digest, and stored only
as its own Artifact, never again inside the pack, as ADR 0036 already keeps
media outside its bundle. The pack's container is ADR 0036's unchanged
(deterministic POSIX PAX over USTAR, the same header values, ordering,
padding, and refusals: symlinks, special files, path collisions, NUL or
newline in a path, an entry over the USTAR size limit), with these entries:

```text
manifest.json
project/<path relative to the project folder, for members kept in the pack>
```

`manifest.json` is RFC 8785 canonical JSON: `format`, every member of the
folder with its path, byte length, `sha256:` digest, and `stored` (`pack`
for a member inside the tar, `artifact` for one admitted individually), in
ascending UTF-8 order of path, and `excluded`, the patterns the command left
out.

Two formats exist. `archive.project-snapshot/2` is the slim pack. Until its
writer is built, the command may write `archive.project-snapshot/1`, a
**whole** pack holding every member, audio included (`stored` is `pack`
throughout). The snapshot claim names which (`format`, below), because Door
never reads a pack and the destroy rule differs (Reach and destroy). It carries no
object identifier, time, or name, so packing an unchanged folder gives the
same bytes and the same content digest (INV-5). Exclusions are command
configuration (mechanism); the defaults are `.DS_Store` and `._*`, which
the operating system rewrites without any change to the project, and the
manifest lists every pattern applied, so a pack says what it leaves out
(INV-24).

The command reads the folder as ADR 0036 reads a frozen tree: it copies it
to staging, scans before and after, and refuses the snapshot if anything
changed during the read (an autosave), asking the operator to save and try
again. It never writes into the operator's project folder.

### `archive.claim/project_snapshot` version 1

A **project snapshot** is a claim that one pack is a state of one project
the operator chose to keep. Its references:

| Role | Count | Pins |
|---|---|---|
| `project` | 1 | a `daw_project` Entity revision |
| `snapshot` | 1 | the snapshot pack's Artifact revision |
| `audio` | 0 to 10,000, distinct | Artifact revisions: the pack's audio members, each admitted individually |
| `midi` | 0 to 10,000, distinct | Artifact revisions: the pack's Standard MIDI Files, each admitted individually as an `audio/midi` document (ADR 0085) |
| `work` | 0 to 16, distinct | `musical_work` Entity revisions the project is a production of |

Its payload:

```json
{
  "taken_at": "2026-10-19T21:04:00Z",
  "format": "archive.project-snapshot/2",
  "name": "before the vocal comp",
  "daw": {"name": "Ableton Live", "version": "12.1"},
  "open": "Harbor Light.als",
  "audio": [{"path": "Samples/Recorded/Vox 0003.wav"},
            {"path": "Samples/Imported/kick_hard.wav"}],
  "note": "comped verse 2; chorus vocal still rough"
}
```

- `taken_at` (required): when the command read the folder, an RFC 3339
  UTC instant from the snapshotting machine's clock. It is the asserter's
  statement and orders a project's snapshots; it is never Door's `at`.
- `format` (required): the pack's format, `archive.project-snapshot/1`
  (whole) or `archive.project-snapshot/2` (slim). World checks it against
  the manifest where the pack's bytes are present.
- `name` and `note` (optional): the operator's words, 1 to 200 and 1 to
  2,000 characters.
- `daw` (optional): the DAW's name and version as free text, 1 to 200
  characters each, so a later reader knows what to open the pack with. It
  is not parsed and identifies nothing.
- `open` (optional): the member path of the project file to open, as the
  operator or configuration names it; the command does not find it by
  parsing.
- `audio` (required when there are `audio` references): one entry per
  `audio` reference, in the same order, giving that file's member path;
  `midi` likewise for the `midi` references (Settled 6).

**Door rules** (a specification release adds them with the bundle): one
`project` of kind `daw_project`; one `snapshot` Artifact; a known
`format`; distinct `audio` and `midi` Artifacts, none with the snapshot's
content digest; `audio` and `midi` entries as many as their references; paths relative, non-empty, without `.` or `..`
components, NUL, newline, or a leading `/`, distinct, and at most 4,096
bytes, across both lists; `open` not among them; the envelope `agent` the active
operator's person, since the command acts for the operator and wave 2 gives
snapshots to no one else. A supersession keeps its agent (D-3) and every reference, so a
snapshot can be renamed or its note corrected but never repointed at other
bytes. Door never reads the pack (INV-2): that each `audio` and `midi`
path is a manifest member with that Artifact's digest and size is checked
by World on read where the pack's bytes are present (below) and by
`restore`, never assumed.

**Audio is admitted individually.** Every member whose extension is in the
command's configured audio list (by default `wav`, `aif`, `aiff`, `flac`,
`mp3`, `m4a`, `ogg`, `caf`) is admitted as its own Artifact, or, when its
content is already an Artifact's (a take also imported from the recorder by
`capture import`, a sample already in a collection, the same file in an
earlier snapshot), the existing Artifact revision is pinned, as `capture
import` skips known content. That is the deduplication W2.5 relies on: the
same take in ten snapshots is one Artifact and one annexed file, and with
slim packs no snapshot stores it again. Members ending `.mid` are admitted
the same way, as `audio/midi` documents with their document source, and
pinned under `midi` (Settled 6).

**Origin of project audio.** For members under path prefixes the operator
marks as recorded takes in the command's configuration (a DAW's
recorded-takes folder), the command proposes `own_capture` under the
operator's person, itself as `by`, as capture grammars are marked as the
operator's own devices (ADR 0096, R4.10). Every other member gets no origin
claim and is unknown origin until someone says (Settled 4).

**Friends' projects.** Wave 2 gives snapshots to the operator only. A
friend's project is snapshotted by the operator, who records the friend as
`depositor` on origin claims of its audio (ADR 0096) (Settled 7).

**One batch.** A snapshot is one `door admit` batch: the `daw_project`
Entity on the first snapshot, the pack, each new audio Artifact, and the
snapshot claim. When the pack's digest equals the digest of the project's
latest snapshot pack (by `taken_at`), the command writes nothing (INV-5).
A folder reverted to an earlier state is a new snapshot claim pinning the
existing pack Artifact, since the operator asked and the order changed.

**Finding the project again.** `--project <id>` always names the project.
For convenience the command keeps a map from folder path to project in its
operational state (INV-4 does not apply; losing it costs typing the
identifier once); it never writes a marker file into the operator's folder
(Settled 5).

**Restore.** `restore <snapshot> --into <empty directory>` fetches the pack
and, for a slim pack, every Artifact the snapshot claim pins under `audio`
and `midi`, checks every member's length and digest against the manifest,
and writes the folder; it refuses a directory that is not empty and reports a missing
or mismatched member as an error (INV-24), as ADR 0036's lookup does. It is
read-only toward the archive.

### Bounces link by lineage

A **bounce** is an Artifact the DAW exported from a project. It is an
ordinary audio Artifact with an ADR 0098 lineage claim whose `input` is the
snapshot pack it was bounced from (adding audio Artifacts or selections as
further inputs when known, and `complete: true` when the operator says so).
The command's `bounce <folder> <file>` takes a snapshot first when the
folder differs from the latest one (else pins the latest), then admits the
bounce and proposes its lineage claim in the same batch, as ADR 0098 asks of
Archive's own tools. A bounce admitted another way (through `capture
import`, or a member's admission through the relay) is linked by asserting
the lineage claim afterwards; anyone with the `cut` grant may, over a pack
their audiences can read (ADR 0098). A bounce found inside the folder is
admitted as audio like any member; whether it is a bounce is the operator's
lineage claim, not the command's guess.

### Reach and destroy

**Reach.** World's pin map (`_pins` in `world/documents.py`) gains, for
every project snapshot revision, an edge from its `snapshot` pack to each
`audio` and `midi` Artifact and to its `project`, as `document_source` adds an edge
from a document to what it was `derived_from`; Door's `Archive.Door.Reach`
gains the same edge. The pack holds or rebuilds those files' bytes, so it
reaches no further than the strictest of them; a sensitivity label on the project caps
every snapshot of it; and a bounce, whose lineage reaches the pack, reaches
no further than any sample or take the project held (W1.13). Every edge
narrows (INV-18). An `archive.claim/origin` on the pack or bounce stays
ADR 0096's business: the operator asserts a bounce's origin directly, and
lineage only caps reach.

**Destroy.** Installing this contract extends the reachability D-5 asks
for. Containment dependents and [ADR 0098](0098-content-lineage.md)'s
lineage dependents are **one destroy computation** (R6.17): when a destroy
claim destroys content D, Door builds one index from input object to
dependents in its replay pass, and D's dependents are the outputs of
ADR 0098's lineage claims together with the snapshot pack of every
committed project snapshot revision whose `audio` or `midi` pins an
Artifact with content D. Each dependent needs exactly one
`dependent_destroy` or `dependent_source_gone` reference, by ADR 0098's
rules; a bounce of such a pack is a lineage dependent of the pack, reached
when the pack itself is destroyed, through the same batch recursion.

- **A whole pack must be destroyed with it (R6.8).** A pack whose snapshot
  claim names `archive.project-snapshot/1` holds D's bytes inside it, so
  `dependent_source_gone` would keep the destroyed bytes in the archive:
  Door accepts only `dependent_destroy` for it.
- **A slim pack may take either.** A pack naming
  `archive.project-snapshot/2` holds no audio bytes; `dependent_source_gone`
  keeps it with the loss recorded, and its `restore` then reports the
  missing member (INV-24).

Destroying a pack leaves its audio and MIDI Artifacts alone: they are
content of their own, not made from the pack.

### World and the relay

- **Projection.** `daw_project` projects as `arch:DawProject`. A snapshot
  projects as an `arch:ProjectSnapshot` resource with `arch:snapshot_of`
  its project, `arch:snapshot_file` its pack, `arch:taken_at`
  (`xsd:dateTime`), and one `arch:contains` per audio or MIDI Artifact with its
  `arch:member_path`; `arch:contains` maps to `dcterms:hasPart` as a
  mapping only (ADR 0012, INV-11).
- **Check on read.** World's `check_project_snapshot` validates the claim
  against its targets. Where the pack's bytes are present, World compares
  the manifest and its `format` with the claim's `audio` and `midi` paths
  and digests and reports a mismatch as a conflict, never silently
  (INV-24).
- **Views.** `projects` lists the projects a caller can read, with each
  one's latest snapshot and count. `project` answers one project: its
  snapshots in `taken_at` order (ties by revision), each with name, note,
  DAW, file to open, audio files (path, digest, Artifact), Works, and
  bounces (lineage outputs naming its pack). `project_changes` compares two
  snapshots of one project by their claims alone: audio paths added,
  removed, or with new content, and whether anything else in the pack
  changed (the manifests differ beyond audio). ADR 0098's `derived` view
  of an audio Artifact also lists the snapshots that contain it ("in
  projects"). All are regenerable (INV-4).
- **Relay.** The relay serves these views within the caller's readable
  set, which narrows by itself through the pin map. It does not serve pack
  bytes in wave 2: restoring is the operator's local command. Project audio
  and bounces are ordinary Artifacts, so their derived media and cuts work
  as for any recording.
- **Enrichment.** By default project audio gets the probe and derived
  media for playback only; heavier extractors (stems, ADR 0102's timbre
  model) run on a file when it is cut, chosen, or named by policy, so a
  busy project's hundreds of files cost little until used (Settled 8).
- **Abilities ([ADR 0094](0094-peoples-writes.md)).** `annotate` covers
  claims about readable project audio, bounces, and projects (judgments,
  span annotations, titles, contributions, origin other than
  `own_capture`). `cut` covers selections over readable project audio and
  bounces, and lineage from a bounce to a readable pack. `admit` lets a
  member admit a bounce as audio, with its origin. No ability covers a
  snapshot: the claim is the operator's, members admit Artifacts and not
  Entities (ADR 0094), and a pack (`application/x-tar`) is outside members'
  staging types. These rows are part of the one ADR 0094 amendment that
  lists every wave-2 ability addition (R6.10).

## Consequences

- The operator can keep the states of a production that mattered, reopen
  any of them years later without the DAW's format being understood by
  Archive, and see which bounce came from which state.
- Every take and sample in a project is a playable, cuttable, enrichable
  Artifact, deduplicated against the recorder's imports and earlier
  snapshots. By default they get only the probe and playback media; a busy
  project can add hundreds of files at once.
- A slim pack stores only what is not admitted individually (the project
  file, settings, presets), so ten snapshots of a 3 GB project add ten
  small packs beside 3 GB of individual audio, and W2.5's "nearly free"
  holds. Git annex keys are whole-file (SHA256E), so a whole pack would
  instead store all its audio again each time; whole packs exist only until
  the slim writer is built, and their audio cannot be destroyed without
  them (R6.8).
- A project that points at audio outside its folder (not "collected" in the
  DAW's terms) reopens without it; the command cannot tell, because it does
  not parse the format, and says so when it finishes.
- A bounce reaches no further than the strictest file in the snapshot it
  came from, whoever asserted the lineage.
- Destroying a take lists every snapshot that holds it, in the same
  computation as its lineage dependents; destroying such a snapshot lists
  its bounces, so nothing made from it survives without a record.
- No invariant changes. The spec release adds the kind, the contract, the
  pack format, and the dependent rule.

## Alternatives considered

- **The project file as the Artifact, audio by reference.** It cannot be
  reopened from the archive alone; W2.5 asks for the whole folder.
- **A snapshot as an Entity.** A snapshot is a state of the project at one
  moment, with exact bytes; a claim relating an Artifact to the project
  says that, as a document's dated exports do (ADR 0085).
- **A project as a `collection` ([ADR 0100](0100-packs-and-collections.md)) of snapshots.** Membership is a
  person's grouping of independent things; snapshots are ordered states of
  one production, with a time and a file to open.
- **Lineage from the pack to its audio.** Lineage has at most 64 inputs and
  says "made from"; a pack contains its audio, which the snapshot claim
  states exactly, with paths.
- **Snapshot on every save.** W2.5 chose explicit snapshots; DAWs save
  constantly and every save would be a new pack.
- **Parsing the project file** to find the audio it uses, its tempo, or its
  bounces. W2.5 rules it out for wave 2.

## New nouns

- **DAW project** (Entity kind `daw_project`). (1) It names one production
  across its saves, which neither a session (one occasion) nor a Work (the
  composition) does. (2) No verb; it is snapshotted, which is the command's
  operation, not a verb of a concept. (3) None itself; it
  gives reach one place to cap a whole production (INV-21). (4) It is an
  Entity kind, the existing concept for things claims are about. (5) No;
  its snapshots are claims. (6) A detail of the core context.
- **Project snapshot** (`archive.claim/project_snapshot`). (1) It says a
  pack is a kept state of a project and which of its members are which
  audio Artifacts; nothing else relates a container's bytes to individually
  admitted content. (2) It owns no verb; the snapshot command proposes it.
  (3) It lets reach keep INV-21 and destroy keep INV-12 for content inside
  a pack. (4) It is a claim contract, read by World's reach and Door's
  destroy check. (5) Its claims are evidence; reach and dependents are
  computed. (6) A claim contract, not a subsystem.
- **Snapshot** as a noun is only this record's project snapshot (R6.17).
  [ADR 0105](0105-notebook.md) speaks of snapshotting notebook pages as an
  operation and names no snapshot object; the vocabulary reconciles the two
  uses when these records are accepted.
- **Snapshot pack** (formats `archive.project-snapshot/1`, whole, and `/2`,
  slim). (1) It makes a folder one content digest, reproducibly. (2) No verb. (3) INV-5, by being
  deterministic. (4) It is ADR 0036's container with a different manifest
  format; the code is shared. (5) No; it is an Artifact's bytes. (6) A file
  format.
- **Bounce** is a word, not a noun: an Artifact whose lineage names a
  snapshot pack. (1) to (6): nothing new; vocabulary may record the word.

## Touch points

- `door/src/Archive/Door/Contracts.hs`: `validateCoreContext`,
  `validateCoreContextForSpecification`, and a new context key list after
  `creditContextKeys` for `daw_project`.
- `door/src/Archive/Door/Reach.hs`: `indexHeld` and `closure` gain the pack
  edges.
- `door/src/Archive/Door/TransitionDryRun.hs`: `validateDependents` gains
  containing packs in the same dependent index as ADR 0098's lineage
  outputs, and refuses `dependent_source_gone` on a whole pack.
- `world/documents.py`: `_pins` (pack to audio and project), read by
  `Reacher` and `reach`.
- `world/projects.py` (new): `check_project_snapshot`, the manifest
  comparison, and the three views' queries.
- `world/contract_graphs.py` and `world/projection/6/vocabulary.ttl`,
  `domain.ttl`: the snapshot graph, `arch:DawProject`, and its terms.
- `world/record_views.py` and `world/views.py`: `VIEWS` gains `projects`,
  `project`, and `project_changes`; ADR 0098's `derived` lists containing
  snapshots.
- `world/circle.py`: `MEMBER_CONTRACTS` unchanged (no member snapshots);
  `readable_set` unchanged, through the Reacher.
- `capture/project.py` (new): `snapshot`, `bounce`, `restore` (slim packs
  rebuilt from their pinned Artifacts), the folder-to-project map, the
  recorded-takes prefixes, using
  `enrich/adapter.py`'s `object_id` and `ARTIFACT_NAMESPACE`; the tar
  writer shared with ADR 0036's bundle code (`_tar`, `_header`,
  `_pax_path` in `migration/v2_bundle.py`) and its checked read
  (`open_verified_bundle` in `migration/v2_lookup.py`).
- `docs/boundaries.md`: the new command lines.
- `spec/archive-spec.md`: D-8 (the kind), D-5 (containment dependents),
  the contract bundle and its profile rules, and the pack format;
  `docs/vocabulary.md`: *DAW project*, *project snapshot*, *snapshot pack*,
  *bounce*.

## Settled by the operator (2026-10-19)

1. **A second new kind.** `daw_project` is accepted as wave 2's second new
   Entity kind (R6.7).
2. **Whole packs and storage.** Snapshot packs are slim: audio admitted
   individually is left out and listed by digest; restore reassembles the
   folder from the pack and the pinned Artifacts (R6.8).
3. **Destroy and whole packs.** Until slim packs exist, destroying a
   project's audio requires `dependent_destroy` for each whole pack holding
   it; containment and lineage dependents are one destroy computation
   (R6.8, R6.17).
4. **Origin of project audio.** `own_capture` under the operator's person
   for members under path prefixes the operator marks; no origin otherwise
   (R6.16).
5. **Finding the project again.** `--project <id>` always works; a
   folder-to-project map in operational state; no marker file (R6.16).
6. **MIDI files in the folder.** Admitted individually as `audio/midi`
   documents, pinned under a `midi` role (R6.16).
7. **Friends' projects.** Not in wave 2; the operator snapshots them,
   recording the friend as `depositor` on its audio's origin claims
   (R6.16).
8. **Enrichment of project audio.** Probe and derived media by default;
   heavier extractors when a file is cut, chosen, or named by policy
   (R6.16).
9. **The word "snapshot".** A noun only for project snapshots; ADR 0105's
   snapshotting is an operation; reconciled in the vocabulary on acceptance
   (R6.17).
