# Archive

A self-hosted archive of a band's rehearsals: Door admits records, World relates them to the outside world, Enrichment measures them. `docs/vocabulary.md` is the authoritative vocabulary and its naming-discipline table binds (AGENTS.md); this file restates the terms from that table, in the glossary format tools read, and carries only the aliases that are wrong wherever they appear. Where the two disagree, `docs/vocabulary.md` wins.

## Language

### Authorities

**Door**:
The only authority for archival meaning: it admits new things and transitions archival state, judging validity, never desirability, and never moving bytes.
_Avoid_: validator, gatekeeper, write service

**Admit**:
What Door does to bring something new into the archive. The capture importer still imports (it proposes to Door); only Door admits.
_Avoid_: ingest, upload

**World**:
A rebuildable projection that relates the archive's evidence to the outside world and answers queries; it owns nothing.
_Avoid_: knowledge base, facts store, metadata store, graph database

**Enrichment**:
Deriving new evidence from existing evidence without changing its inputs: a transcript, a loudness measure, a format identification.
_Avoid_: pipeline, analysis service

**Policy**:
The operator's declaration of what the archive should hold, in a small rule language; configuration, which tells a program how to run, is something else.
_Avoid_: rules engine

**Plan / apply**:
Plan works out what should change to meet policy, and why; apply carries it out.
_Avoid_: sweep, orchestrator

### Records

**Record**:
One immutable revision: an artifact, entity, claim or assessment.

**Claim**:
A typed, versioned statement about other things: evidence, never fact.

**Assessment**:
A judgment of an exact claim revision.
_Avoid_: ruling

**Selection**:
A claim naming accepted audio: one excerpt over every simultaneous source of a session. Its identity follows the intended excerpt, not its coordinates or digest. A take is named by its selection or song occurrence, never a record kind of its own.

**Song occurrence**:
A claim pinning the selection(s) of one rendition; two takes are two song occurrences, even of the same Work. Plain occurrence means one arrival of content, so always say song occurrence for a rendition.

**Session**:
An Entity of kind `recording_session`, its parts ordered by `session_parts` claims; simultaneous mics and line inputs are sources of one session.

**Stem**:
One separated signal of a recording (vocals, drums, bass, other), a cited feature-bank output, never admitted; its opposite is the mix.

**Feature**:
A measurement of content one enrichment tool makes for others to read; never a claim, never admitted.

**Capture start**:
When a file's recording began, held as a claim and allowed to be unknown. Door's commit time (`at`) is never capture time.
_Avoid_: recording time

### Disclosure

**Exposure class**:
How far a destination shows content: private, circle, service, open or distribution.

**Audience**:
A relay's named group of people: a destination of class circle whose members the archive records.
_Avoid_: relay circle

**The archive's holdings**:
Everything an archive has admitted. Collection is an Entity kind.
_Avoid_: the collection

**Project snapshot**:
A saved state of a DAW project; the only snapshot that is a noun. Snapshotting is an operation.
