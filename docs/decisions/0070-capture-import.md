# 0070. New recordings are imported natively

Status: accepted

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
