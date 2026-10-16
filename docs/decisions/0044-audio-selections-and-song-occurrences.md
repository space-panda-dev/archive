# 0044. Audio selections and song occurrences have separate identities

Status: accepted; amended by [ADR 0066](0066-span-suggestions.md) (boundary candidates may be admitted)

The rehearsal review application needs an accepted excerpt of audio that
matching, transcription, derivation, publication, and World can all name.
The V1 `song_performance` record combines that excerpt with assertions about
what happened in it. A sibling project's V2 decision separated these concepts, but the
Archive contract must make its own decision under ADR 0015. In particular, a
title cannot establish a musical occurrence or a Work identity (ADR 0042).

## Decision

**An audio selection names accepted audio.** It is an Archive Claim over one
or more ordered, half-open spans. Every span pins the exact Artifact revision
whose samples it selects and uses integer sample offsets with their sample
rate (ADR 0003). One selection may cross an artifact boundary, including a
recorder rollover. The selection alone says nothing about whether the audio
is a song. Boundary candidates and unaccepted review drafts remain outside
the archive.

**Selection identity follows the intended excerpt.** Correcting the bounds
or source pins while still selecting the same intended excerpt explicitly
supersedes a revision of the same selection object. Selecting a different
excerpt, including another take of the same Work, creates a different object.
Neither coordinates, filenames, titles, nor content digests supply its
identity. Every old revision and its provenance remain available.

**A song occurrence names one rendition.** It is an optional Claim that pins
one or more exact audio selection revisions. Two microphone views of the same
rendition may belong to one occurrence; two separate takes are two
occurrences even if both perform the same Work. A single accepted selection
gets an occurrence only when the operator explicitly asserts a rendition or
needs to link that rendition to a durable Work. A title or a generated review
candidate does not mint one automatically. Its object ID
remains stable when a selection is corrected, but its exact pins do not change
silently: changing them requires an explicit occurrence revision.

**Work identity is a separate assertion.** An `archive.claim/performance_work`
claim pins an exact song occurrence Claim revision and an exact musical Work
Entity revision (ADR 0042). World may traverse that chain while preserving
each assertion, its author, and competing claims. It must not interpret an
arbitrary Claim revision as a song occurrence, infer a Work from a title, or
choose a current rendition without an accepted per-kind rule.

**The review interface is later work.** This decision defines durable
meaning, not the PWA gesture, draft workflow, or visual design by which an
operator asserts a rendition. One gesture may eventually propose several
records, but Door admits each according to its contract (ADR 0002).

## Contract boundary

This decision does not choose the serialized representation that binds each
ordered span to its exact Artifact reference, the occurrence's selection
membership encoding, or domain assessment and current rules. Those need a
closed contract and Door validation before any such Claim is admitted. The
existing specification's `performance_work` target check is only structural;
it does not yet establish that the target is a song occurrence. A later
specification release must tighten that check and World must respect it.

No current Archive evidence is rewritten by this decision. Translating V1
`song_performance` remains a per-record accounting task: a record equivalent
to one selection may fold into claims about that selection, a real grouping
may become an occurrence, and ambiguity remains unresolved rather than
inventing an occurrence (ADR 0015).

## Alternatives considered

- **One performance record for every titled cut.** This duplicates the
  selection and asserts songhood from a label.
- **Derive selection identity from bounds or source files.** A correction
  would then change identity, and moving or renaming files could appear to
  change the selected excerpt.
- **Move occurrence pins automatically when a selection changes.** This would
  silently change an admitted Claim and erase the exact evidence it cited.
