# Proposed V2 migration release boundary

Status: proposed; no specification release is made by this document.

Archive 0.0.3 and 0.0.4 contain World contracts. The V2 migration route is
reserved for a later specification release under
[ADR 0046](../decisions/0046-world-release-before-v2-migration.md); its version
will be chosen when its exact contracts and source translation rules are
accepted. The accepted
[ADR 0036](../decisions/0036-v2-frozen-migration-bundle.md) has a complete
normative amendment in
[0036-v2-frozen-migration-bundle-spec.md](0036-v2-frozen-migration-bundle-spec.md).
That amendment can be carried into the next specification as written. It
defines the deterministic PAX/USTAR bundle, canonical manifest, fixed first
bundle identity, source locators, and lookup behavior. It does not establish
the exact migration claim contracts that ADR 0036 leaves open.

The accepted [ADR 0040](../decisions/0040-audio-section-song-route.md) requires
four canonical contract bundles in the same specification release:

- `archive.audio/song_entity`;
- `archive.migration/audio_selection`;
- `archive.migration/audio_song_assertion`; and
- `archive.migration/audio_song_match`.

The four contracts and their context fragments are not installed here. The
audio route proposal
([0040-audio-section-song-route.md](0040-audio-section-song-route.md)) does
not yet define the full source-to-Archive translation rules needed to produce
exact, safe bundles and a normative release. The accepted audio decision
clarifies the source vocabulary; the remaining boundaries below concern how
that evidence maps into Archive contracts. They must be resolved before a
release can include that route.

## Audio route blockers

[ADR 0047](../decisions/0047-v2-audio-route-source-rules.md) answers all five
blockers below. It leaves multi-segment performances unresolved and keeps a
performance whose history leaves `reviewed` unresolved until a retraction
contract exists. The blockers are kept as the record of what each answer
closes.

1. **A segment record ID is not an enduring selection identity.** The accepted
   audio decision is
   `specs/decisions/2026-09-27-audio-capture-selections-and-song-occurrences.md`.
   It distinguishes durable audio selections from historical `audio_segment`
   records. In the source writer, `segment_id` is minted for each record and a
   correction points to its predecessor with `supersedes_segment_id` (source
   module `segment.py`, functions `segment_id()` and the record builder).
   Therefore the
   proposed direct allocation `v2-segment-<source-id-hex>` would make each
   source record ID an Archive object identity without establishing that the
   Archive object means that record or an enduring selection. A migration rule
   still must define when an explicit predecessor chain represents revisions
   of one Archive selection, and how forks, missing predecessors, and
   contradictory chains remain unresolved. The source decision does not
   supply that migration identity rule (ADR 0021, ADR 0026, ADR 0035).

2. **Performance identity is known, but its claim meaning is not automatic.**
   The source module `song_performance.py` function
   `derive_performance_id()` deterministically hashes the source
   artifact ID and sorted segment IDs; title and mutable metadata are excluded
   (source module `song_performance.py`). Records
   with one `performance_id` can have multiple same-ID revision snapshots, and
   `supersedes_performance_id` links a different membership identity to its
   predecessor. This establishes which source identifier names a performance
   and supplies its revision history. It does not make that identity a song
   entity or prove that a titled performance identifies one. The migration
   contract still must define how a reviewed source performance supports the
   separate Archive assertion, how same-ID source snapshots map to claim
   revisions, and how membership-changing successors are accounted for. Song
   entity identity remains human-proposed under ADR 0040; a title or
   performance hash cannot supply it.

3. **The performance state set is exact; historical segment state is not.**
   `song_performance.state` is exactly `suggested`, `reviewed`, or `retracted`
   (source module `specs.py`). A contract can
   map only `reviewed` to an affirmative source assertion and keep the other
   two non-affirmative while preserving all rows. Separately,
   `audio_segment.review_state` has only a known `reviewed` writer value; its
   complete historical vocabulary is deliberately not enum-checked in that
   source module. The selection route therefore must treat missing or unknown
   historical segment states as unresolved. It must not infer that a segment
   was reviewed merely because current writers only emit `reviewed`.

4. **Source boundaries cannot yet be converted to S-15.** The source writer
   rounds `start_seconds` and `end_seconds` to six decimal places and stores no
   sample rate (source module `segment.py`). The
   accepted source audio decision does not define a sample-boundary conversion.
   A six-decimal seconds value is not itself an integer sample offset. A
   selection can be converted only when its pinned artifact's sample rate and
   an accepted boundary rule establish one exact integer offset; otherwise it
   remains unresolved under S-15 and INV-24.

5. **The match payload needs a closed input model.** The proposal requires the
   score's exact run inputs, pins, method, rule, and optional saved-report
   locator, but its candidate payload does not provide a complete closed
   representation for every run input and its pin. The canonical schema must
   identify that full shape before an `audio_song_match` digest can be
   released. A disposable report is not evidence in the migration bundle; a
   recomputed match must cite its actual run and input pins.

These are migration-contract and source-boundary gaps, not reasons to weaken
the accepted route. A migration reader must fail closed for each affected row,
retain its source bytes and locator, and report the unresolved reason. It must
not treat a record ID as a selection identity, treat a performance ID as a
song identity, infer review state, infer currentness, or convert a sample
boundary from incomplete source facts.

## Release composition

Specification 0.0.5 installs the route as ADR 0049 reshaped it: the native
review contracts of ADR 0048, plus `archive.migration/translation`,
`archive.migration/selection_translation`, and `archive.claim/work_match`.
ADR 0040's four bundles are not released. No `work_match` claim can be
admitted until a run record contract exists. The settled 0036 amendment is
still to be integrated into a specification text; 0.0.5 does not carry it.
Migration between spec versions remains open, so an existing 0.0.4 archive
cannot gain these contracts. World 0.0.3 and 0.0.4 do not install them.

When the release gates are met, the release must preserve prior release
resources byte-for-byte, create a new versioned specification directory with
its exact `VERSION`, specification, and local core context, add the four
contract bundles at their canonical SHA-256 paths, and set the source
specification and `spec/VERSION` to that release's version.
The migration code may propose the route only after that release and the
source-specific identity, state, and boundary premises are verified.
