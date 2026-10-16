# 0046. Sequence World contracts before the V2 migration route

Status: accepted

The World branch made specification 0.0.3 available for musical Works and
correspondence claims, then 0.0.4 for audio selections, song occurrences, and
typed occurrence-to-Work claims (ADRs 0041, 0042, 0044, 0045). At decision
time, main still selected 0.0.2. Its proposed
[V2 migration release readiness note](../proposals/v2-migration-release-readiness.md)
had reserved the next release for the first V2 migration bundle and audio route,
including four migration contract bundles. The audio route remains blocked by
source identity, review-state, sample-boundary, and match-input questions.

Merging the World branch unchanged would publish a different 0.0.3 and 0.0.4
release sequence. That would make the readiness note's version and release
composition false. It would also leave the first V2 semantic shadow unable to
claim the audio route required by ADRs 0036 and 0040. This is a sequencing
decision, not a merge conflict that can be settled by choosing one line of
text.

## Decision

Release the World Work contracts as 0.0.3 and the selection, occurrence, and
typed Work contracts as 0.0.4. Supersede the version assignment and release
sequencing in the proposed readiness note. Keep the V2 migration bundle and
four `archive.migration/*`/`archive.audio/song_entity` contracts gated for a
later specification release, whose version is chosen when those exact bundles
and source translation rules are accepted. Until then, the first V2 semantic
shadow reports its audio route unresolved; neither Archive nor World treats a
bundle lookup as a semantic claim.

The World audio selection and song occurrence contracts describe native
Archive assertions. They do not authorize a V2 translation. In particular,
`archive.claim/audio_selection` is not interchangeable with
`archive.migration/audio_selection`; source IDs, review states, and rounded
seconds do not supply the missing migration identity or sample conversion.
The `musical_work` Entity used by World and the distinct `song` Entity proposed
for the migration route also remain separate until an accepted mapping
defines their relationship. No per-kind current rule or content-bearing
admission gate changes here.

## Consequences

The proposed readiness note and open questions name the later V2
release without calling it 0.0.3. Keep ADRs 0035, 0036, and 0040 and their
unresolved source gates intact. World 0.0.3 and 0.0.4 are independent releases.
The first V2 semantic shadow remains blocked until the four migration bundles
and exact translation rules are ready.

## Alternative

Defer World contract releases and merge only read-only export/projection
mechanism against specification 0.0.2. This preserves the proposed 0.0.3
sequence, but World cannot admit or query its typed Work, selection, and
occurrence chain in a newly initialized Archive yet.
