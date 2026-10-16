# 0049. V2 migration route contracts

Status: accepted

ADR 0040's audio route had four migration contracts. ADR 0047 then set the
source rules, and ADR 0048 changed the route's shape. A reviewed V2
performance now becomes a native `song_occurrence` linked to a musical Work
by `performance_work` version 2, and a song is a `musical_work`. ADR 0036
requires every translated claim to carry a pinned bundle reference and a
source locator in its own contract payload. The native contracts cannot hold
a locator: their payloads are closed. Separate migration variants would give
World two chains, and would make a migrated occurrence unlinkable by the
native `performance_work`, which accepts only native occurrences.

The operator chose on 2026-10-14 to translate into native claims with a
separate translation record. Songs and artists are identified once, by the
operator, on a confirmation sheet. The V2 operator maps to the operator's
person Entity.

## Decision

**Translated claims use the native contracts.** Migration proposes
`audio_selection` version 2, `song_occurrence`, `selection_kind`,
`selection_title`, `artist_credit`, `performance_work` version 2, and
`archive.assessment/judgment`, exactly as the review app would. The review
app and World therefore see one chain, whatever its origin.

**A translation record carries the provenance.** In the same Door batch as
the claims it translates, migration admits one
`archive.migration/translation` claim per V2 source record. It pins every
translated claim or assessment revision, and the bundle Artifact revision in
role `migration_source`. Its payload carries ADR 0036's closed locator
(member, member digest, offset, length, record digest), the V2 schema and
source ID, and the rule that produced the translation. A selection uses
`archive.migration/selection_translation` instead. It adds the chain root
and position (ADR 0047 rule 1), the source seconds as exact decimal text, the
sample-rate probe's tool, version, and rate, and the `round-half-even/1`
boundary rule (ADR 0047 rule 4). This amends ADR 0036's requirement that the
locator be in the translated claim's own payload: it is in a record that
pins that exact claim revision, admitted in the same batch. Every translated
claim's `by` is the migration program's Package URL, so a translated claim is
recognisable even before its translation record is read.

**Rules.** Each rule maps one V2 source shape:

| Rule | V2 source | Archive records |
|---|---|---|
| `v2-audio-segment-chain/1` | one `audio_segment` record | one revision of an `audio_selection` (v2, one track) |
| `v2-song-performance/1` | one `song_performance` snapshot | one revision of a `song_occurrence` (ADR 0047 rule 2) |
| `v2-performance-review/1` | `state: reviewed` | an `affirmed` judgment of each translated claim (ADR 0048) |
| `v2-performance-kind/1` | `kind` | a `selection_kind` claim; a missing kind makes none |
| `v2-performance-title/1` | `title` | a `selection_title` claim; a title is evidence, never identity |
| `v2-performance-artists/1` | `artists` | an `artist_credit` claim in V2 order |
| `v2-operator-identification/1` | the operator's sheet answer for that performance's title | a `performance_work` link to the confirmed Work |

**Songs and artists are identified once, by the operator.** Before the
first semantic shadow, the operator confirms on a sheet which V2 titles name
the same song, and what each artist name is. Each confirmed song becomes one
`musical_work` Entity, with ADR 0040's random `song_key` identity, and the
operator as `agent`. Each artist becomes one `person` or `organisation`
Entity. The `performance_work` links are the operator's assertions made on
the sheet's date. Their translation record cites the V2 performance whose
title the operator identified. The review app owns first identification from
then on (ADR 0040).

**The V2 operator is the Archive operator.** V2 records name the operator by
login. Translated human assertions name the operator's person Entity, created
by `archive init --operator`, as `agent` (ADR 0036). Machine-made V2 records
keep the migration program as `agent`.

**Machine matches.** `archive.claim/work_match` version 1 replaces
`archive.migration/audio_song_match`. It pins the compared occurrence
revision, the Work, the corpus occurrence revisions, an optional parameters
Artifact, and the run that produced it. Its payload has the method and
version, and the score as exact decimal text with its scale and direction
(ADR 0047 rule 5). Because no run record contract exists yet, no match claim
can be admitted. V2 scores stay in the bundle, unresolved, as rule 5 requires.

**The bundle needs content admission.** The frozen bundle is an Artifact
whose bytes Door must admit. The selections' audio Artifacts are admitted the
same way. Both use the content admission that ADR 0023 describes.

## Consequences

ADR 0040's `archive.audio/song_entity`, `archive.migration/audio_selection`,
`archive.migration/audio_song_assertion`, and
`archive.migration/audio_song_match` are not released. The candidate bundles
are in [0049-contracts](../proposals/0049-contracts/). The migration code
fails closed for every row that no rule covers, as before (INV-24).

## Alternatives considered

- **Migration-variant contracts with inline locators.** This keeps ADR 0036's
  wording, but doubles the contract set and requires every native target
  rule and World query to accept both forms.
- **Shadow without Work links.** This fails ADR 0036's requirement that the
  semantic shadow show song identification.
- **Mint Works from normalised titles.** Titles are evidence, not identity
  (ADR 0042).
