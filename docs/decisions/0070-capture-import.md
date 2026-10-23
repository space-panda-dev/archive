# 0070. New recordings are imported natively

Status: accepted; amended 2026-10-22 (one track of a twin take, and sessions
for migrated parts; accepted by the operator, 2026-10-22)

The shadow is a migrated copy of the V2 archive; its newest recording is the
frozen bundle's newest. New rehearsals still enter the V2 system, and V2 records
added after the frozen bundle cannot be migrated without a new identity and
accounting rule (ADR 0036). Archive has no importer of its own, so nothing
can put a new recording in front of a review client, and nothing exists to
replace the V2 import at cutover. The operator chose a native importer on
2026-10-16.

## Decision

**`capture/` imports a directory of recorder files.** The importer:

1. hashes every file and skips any whose content is already an Artifact's,
   so importing the same card twice changes nothing;
2. groups the rest into sessions and sources with a recorder grammar (a named
   module per recorder model: its file names, track suffixes, and rollover
   rule). A file no grammar recognises is refused unless the operator names
   its session explicitly;
3. adds the bytes to a staging git-annex repository and admits, in one
   content-bearing batch per session (ADR 0023): an Artifact for each file,
   the `recording_session` Entity, and one `archive.claim/session_parts` per
   source;
4. runs the enrichment chain on each new Artifact, each run its own batch:
   audio stream facts (ADR 0065), then span suggestions on the session's
   primary source (ADR 0066); and makes the derived media (ADR 0069).

**A session has one parts claim per simultaneous source.** Each lists that
source's files in recording order. A recorder grammar orders the sources,
and its first is the primary source: the one the detector reads, and whose
first part's content names the session (`session-<sha256>`, ADR 0048).
Multiple microphones and line inputs are sources of one session, never
separate sessions.

**The importer never changes its input.** It reads the files and leaves them;
deleting a card is the operator's act.

**Running it against an archive is an operator command.** Scratch archives
are covered by the standing approval of ADR 0061. The shadow and the real
archive need approval per run until the operator grants a standing one.

## Consequences

- New rehearsals can reach the shadow before cutover, beside the V2 system, as
  Archive-native records. A rebuilt shadow loses them until they are imported
  again, which the first step makes safe.
- Reviews made in the V2 system of recordings after the frozen bundle are not
  migrated. The cutover plan has to choose between reviewing those recordings
  only in the new client and accepting a later-import rule.
- Content-bearing admission needs a staging repository on the archive
  machine, as the INV-5 gate measured.

## Alternatives considered

- **Re-migrate V2 records added after the frozen bundle.** It keeps the V2 system
  as the only way in and needs a second identity and accounting rule.
- **One session per source.** Two microphones of one rehearsal would be two
  sessions, and a selection across both would cross sessions.

## Amendment, 2026-10-22: one track of a twin take, and sessions for migrated parts

Status: accepted by the operator, 2026-10-22.

The operator's recorder writes every take twice: a `TrLR` and a `TrMic`
file with the same clock, the same length, and the same size, carrying what
the operator treats as the same sound. Importing both doubles every later
cost (stems, the feature bank, listening copies) for nothing, and the V2
migration already brought in some takes as `TrMic` only, some as `TrLR`
only, and some as both. Separately, a night that the recorder split into
parts can have its first part migrated from the V2 system, with no
`recording_session`, and its later parts not: the importer refuses that
night today, because some of its files are known.

**A grammar may declare its sources twins.** The Zoom H4essential grammar
does, by default: `TrMic` is a twin of `TrLR`. For a twin take the importer
keeps exactly one file, and keeps the same one every time:

1. if a file of the take is already an Artifact, that Artifact is the take
   (the mix when both are), and no twin of it is imported;
2. otherwise the mix (`TrLR`, the grammar's first source) is imported, and
   its twin is passed over and reported, with the reason, beside the hidden
   names the importer passes over.

A twin that is already an Artifact beside its kept take is not left
unexplained: it becomes the session's second source, cited by its own
`archive.claim/session_parts` claim, so the archive says what it is. A
grammar constructed without twins keeps every source, as before.

**A session may adopt Artifacts that belong to no session.** When some of a
session's files are already Artifacts and none of them is a part in any
`archive.claim/session_parts` claim, the importer proposes the session as
usual, and its parts claims cite those existing Artifact revisions in
recording order, beside the new Artifacts it admits. Only the new files are
staged and enriched. A session whose files are all Artifacts without a
session is adopted the same way, with no content at all. A known file that
already belongs to a session still refuses the import, as before: changing a
session's parts would need a new revision of its claims, which this importer
does not propose.

**The background analyses a take once.** `enrich backfill` leaves out every
Artifact that is a part of a session's later source: one whose parts claim's
first part does not name the session (`session-` and that part's content
digest, ADR 0048). That covers twins kept as second sources. It would also
leave out a genuinely different second microphone, which no grammar
recognises today; a recorder that has one needs its own decision.

Nothing in the specification changes: `archive.claim/session_parts` version
1 already allows a part that is any Artifact revision, and a session with one
source.

**Consequences.**

- A card imported twice, or a night whose first half was migrated, is
  imported without a second copy of any take.
- Takes migrated as `TrMic` only stay `TrMic`: reviews made on them stay
  where they are, and the mix of such a take is never imported.
- Analysis already made on a twin that is now a second source stays in the
  feature bank; it is not run again.
- A night whose parts were migrated as different twins keeps each part's
  migrated track, so one source can list one part's mix and the next part's
  `TrMic`. The operator chose this over importing the missing mix: the
  rule stays one file per take, and reviews made on the migrated track stay.
- Twins are on for every card of the Zoom H4essential grammar. No command
  option turns them off: a recorder fed by a genuinely separate microphone
  needs its own decision first.
