# 0101. A DAW project is an Entity with snapshots, its audio, and lineage to its bounces

Status: proposed (wave 2, drafted 2026-10-19)

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

## Decision

### The Entity kind `daw_project`

A **DAW project** is an Entity of a new kind, `daw_project`: one
production in one DAW, across all its saves. Like `recording_session` it is
a local kind with no Library Reference Model equivalent (P-14), mapped to
`arch:DawProject`. Its `label` is the project's name, which never supplies
identity; its identity is opaque, allocated by the snapshot command on the
project's first snapshot (ADRs 0021 and 0026), never derived from a folder
path or a file name. A specification release adds the kind to the core
context additively (D-8), as 0.0.9 added `release` and `recording`.

### The snapshot pack

A **snapshot pack** is the packed file W2.5 asks for: the project folder,
whole, as one Artifact. Its format, `archive.project-snapshot/1`, is
ADR 0036's container unchanged (deterministic POSIX PAX over USTAR, the
same header values, ordering, padding, and refusals: symlinks, special
files, path collisions, NUL or newline in a path, an entry over the USTAR
size limit), with these entries:

```text
manifest.json
project/<path relative to the project folder>
```

`manifest.json` is RFC 8785 canonical JSON: `format`, every `project/`
member with its path, byte length, and `sha256:` digest in ascending UTF-8
order, and `excluded`, the patterns the command left out. It carries no
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
| `work` | 0 to 16, distinct | `musical_work` Entity revisions the project is a production of |

Its payload:

```json
{
  "taken_at": "2026-10-19T21:04:00Z",
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
- `name` and `note` (optional): the operator's words, 1 to 200 and 1 to
  2,000 characters.
- `daw` (optional): the DAW's name and version as free text, 1 to 200
  characters each, so a later reader knows what to open the pack with. It
  is not parsed and identifies nothing.
- `open` (optional): the member path of the project file to open, as the
  operator or configuration names it; the command does not find it by
  parsing.
- `audio` (required when there are `audio` references): one entry per
  `audio` reference, in the same order, giving that file's member path.

**Door rules** (a specification release adds them with the bundle): one
`project` of kind `daw_project`; one `snapshot` Artifact; distinct `audio`
Artifacts, none with the snapshot's content digest; `audio` entries as many
as `audio` references; paths relative, non-empty, without `.` or `..`
components, NUL, newline, or a leading `/`, distinct, and at most 4,096
bytes; `open` not among the `audio` paths; the envelope `agent` the active
operator's person, since the command acts for the operator and wave 2 gives
snapshots to no one else. A supersession keeps its agent (D-3) and every reference, so a
snapshot can be renamed or its note corrected but never repointed at other
bytes. Door never reads the pack (INV-2): that each `audio` path is a
member of the pack with that Artifact's digest and size is checked by World
on read where the pack's bytes are present (below) and by `restore`, never
assumed.

**Audio is admitted individually.** Every member whose extension is in the
command's configured audio list (by default `wav`, `aif`, `aiff`, `flac`,
`mp3`, `m4a`, `ogg`, `caf`) is admitted as its own Artifact, or, when its
content is already an Artifact's (a take also imported from the recorder by
`capture import`, a sample already in a collection, the same file in an
earlier snapshot), the existing Artifact revision is pinned, as `capture
import` skips known content. That is the deduplication W2.5 relies on: the
same take in ten snapshots is one Artifact and one annexed file. The
command proposes no origin for these files (ADR 0096: a machine never
asserts origin), so each is unknown origin until someone says.

**One batch.** A snapshot is one `door admit` batch: the `daw_project`
Entity on the first snapshot, the pack, each new audio Artifact, and the
snapshot claim. When the pack's digest equals the digest of the project's
latest snapshot pack (by `taken_at`), the command writes nothing (INV-5).
A folder reverted to an earlier state is a new snapshot claim pinning the
existing pack Artifact, since the operator asked and the order changed.

**Restore.** `restore <snapshot> --into <empty directory>` fetches the pack,
checks every member's length and digest against the manifest, and writes
the folder; it refuses a directory that is not empty and reports a missing
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
`audio` Artifact and to its `project`, as `document_source` adds an edge
from a document to what it was `derived_from`; Door's `Archive.Door.Reach`
gains the same edge. The pack holds those files' bytes, so it reaches no
further than the strictest of them; a sensitivity label on the project caps
every snapshot of it; and a bounce, whose lineage reaches the pack, reaches
no further than any sample or take the project held (W1.13). Every edge
narrows (INV-18). An `archive.claim/origin` on the pack or bounce stays
ADR 0096's business: the operator asserts a bounce's origin directly, and
lineage only caps reach.

**Destroy.** Installing this contract extends the reachability D-5 asks
for, beside ADR 0098's lineage dependents. When a destroy claim destroys
content D, its dependents also include the snapshot pack of every committed
project snapshot revision whose `audio` pins an Artifact with content D,
and the output of every lineage claim naming such a pack as `input`. Each
needs exactly one `dependent_destroy` or `dependent_source_gone` reference,
as ADR 0098 sets out; Door builds the index in the same pass. A pack holds
the destroyed bytes inside it, so `dependent_source_gone` on a pack keeps
them; the destroy plan says so beside each such pack (see Open for the
operator, 3). Destroying a pack makes its bounces lineage dependents by
ADR 0098's rule and leaves its audio Artifacts alone: they are content of
their own, not made from the pack.

### World and the relay

- **Projection.** `daw_project` projects as `arch:DawProject`. A snapshot
  projects as an `arch:ProjectSnapshot` resource with `arch:snapshot_of`
  its project, `arch:snapshot_file` its pack, `arch:taken_at`
  (`xsd:dateTime`), and one `arch:contains` per audio Artifact with its
  `arch:member_path`; `arch:contains` maps to `dcterms:hasPart` as a
  mapping only (ADR 0012, INV-11).
- **Check on read.** World's `check_project_snapshot` validates the claim
  against its targets. Where the pack's bytes are present, World compares
  the manifest with the claim's `audio` paths and digests and reports a
  mismatch as a conflict, never silently (INV-24).
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
- **Abilities ([ADR 0094](0094-peoples-writes.md)).** `annotate` covers
  claims about readable project audio, bounces, and projects (judgments,
  span annotations, titles, contributions, origin other than
  `own_capture`). `cut` covers selections over readable project audio and
  bounces, and lineage from a bounce to a readable pack. `admit` lets a
  member admit a bounce as audio, with its origin. No ability covers a
  snapshot: the claim is the operator's, members admit Artifacts and not
  Entities (ADR 0094), and a pack (`application/x-tar`) is outside members'
  staging types.

## Consequences

- The operator can keep the states of a production that mattered, reopen
  any of them years later without the DAW's format being understood by
  Archive, and see which bounce came from which state.
- Every take and sample in a project is a playable, cuttable, enrichable
  Artifact, deduplicated against the recorder's imports and earlier
  snapshots. Policy decides what enrichment runs on them; a busy project
  can add hundreds of files at once.
- Each snapshot pack stores the whole folder again, audio included. Git
  annex keys are whole-file (SHA256E), so bytes inside a pack never
  deduplicate against the individually admitted files or the previous
  pack: a 3 GB project snapshotted ten times holds about 30 GB of packs
  beside 3 GB of individual audio. W2.5's "nearly free" holds for the
  individual files, not for the packs (Open for the operator, 2).
- A project that points at audio outside its folder (not "collected" in the
  DAW's terms) reopens without it; the command cannot tell, because it does
  not parse the format, and says so when it finishes.
- A bounce reaches no further than the strictest file in the snapshot it
  came from, whoever asserted the lineage.
- Destroying a take lists every snapshot that holds it and every bounce of
  those snapshots, so nothing made from it survives without a record.
- No invariant changes. The spec release adds the kind, the contract, the
  pack format, and the dependent rule.

## Alternatives considered

- **The project file as the Artifact, audio by reference.** It cannot be
  reopened from the archive alone; W2.5 asks for the whole folder.
- **A snapshot as an Entity.** A snapshot is a state of the project at one
  moment, with exact bytes; a claim relating an Artifact to the project
  says that, as a document's dated exports do (ADR 0085).
- **A project as a `collection` (ADR 0100) of snapshots.** Membership is a
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
  composition) does. (2) No verb; it is snapshotted. (3) None itself; it
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
- **Snapshot pack** (format `archive.project-snapshot/1`). (1) It makes a
  folder one content digest, reproducibly. (2) No verb. (3) INV-5, by being
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
  containing packs and their lineage outputs beside ADR 0098's dependents.
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
- `capture/project.py` (new): `snapshot`, `bounce`, `restore`, using
  `enrich/adapter.py`'s `object_id` and `ARTIFACT_NAMESPACE`; the tar
  writer shared with ADR 0036's bundle code (`_tar`, `_header`,
  `_pax_path` in `migration/v2_bundle.py`) and its checked read
  (`open_verified_bundle` in `migration/v2_lookup.py`).
- `docs/boundaries.md`: the new command lines.
- `spec/archive-spec.md`: D-8 (the kind), D-5 (containment dependents),
  the contract bundle and its profile rules, and the pack format;
  `docs/vocabulary.md`: *DAW project*, *project snapshot*, *snapshot pack*,
  *bounce*.

## Open for the operator

1. **A second new kind.** W2.3 calls `collection` "the one new noun", and
   W2.5 says a project is an Entity, which needs a kind no existing one
   fits. Recommended: accept `daw_project` as a second Entity kind; W2.3's
   wording was about packs and collections.
2. **Whole packs and storage.** Deduplication does not reach bytes inside a
   pack, so every snapshot stores all its audio again (Consequences).
   Recommended: a *slim* pack, ADR 0036's own precedent of media outside
   the bundle: audio members appear in the manifest with path, size, and
   digest but are stored only as their individual Artifacts, and restore
   reassembles the folder from the pack and the pinned Artifacts. This
   record follows W2.5 as given until the operator chooses.
3. **Destroy and whole packs.** While packs are whole, `dependent_source_gone`
   on a pack keeps the destroyed bytes in the archive. Recommended: Door
   requires `dependent_destroy` for a containing pack while packs are
   whole; with slim packs (2) the bytes are gone and either disposition is
   honest.
4. **Origin of project audio.** Recommended: the command may propose
   `own_capture` under the operator's person for members under path
   prefixes the operator marks in configuration (a DAW's recorded-takes
   folder), as capture grammars are marked (ADR 0096); every other member
   gets no origin.
5. **Finding the project again.** Recommended: `--project <id>` always
   works, and the command keeps a convenience map from folder path to
   project in operational state; no marker file is written into the
   operator's folder.
6. **MIDI files in the folder.** Recommended: admit `.mid` members
   individually too, as `audio/midi` documents (ADR 0085), pinned under a
   `midi` role beside `audio` in the same claim.
7. **Friends' projects.** Recommended: not in wave 2; the operator
   snapshots a friend's project, recording the friend as `depositor` on
   origin claims of its audio.
8. **Enrichment of project audio.** Recommended: by default probe and
   derived media only; heavier extractors (stems, the index's timbre model)
   run when a file is cut, chosen, or named by policy.
