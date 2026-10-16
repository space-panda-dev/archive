# 0050. Operator-confirmed renditions in the V2 migration

Status: accepted

ADR 0047 rule 2 makes each V2 `performance_id` one occurrence. Some V2
performances are not whole renditions. A recorder split cuts one rendition
across two files, and V2 recorded each part as its own performance. A segue
runs one song into another in a single performance. A jam can have a title
without performing any Work. The operator identified these cases on
2026-10-14, while confirming songs for the first semantic shadow (ADR 0049).

## Decision

**An operator-confirmed rendition is one occurrence.** When the operator
confirms that several V2 performances are parts of one rendition, migration
proposes one `song_occurrence` that pins each part's selection revision, in
recording order. Its identity name derives from the first part's
`performance_id`, using rule 2's namespace. Its `agent` is the operator,
because the grouping is the operator's assertion. Each part's V2 snapshot
still gets its own `archive.migration/translation` record under rule
`v2-song-performance/1`, pinning the shared occurrence. No source record goes
unaccounted for. The parts' selections remain separate selections, one per
segment chain (ADR 0047 rule 1). Because `song_occurrence` stores its selections sorted by
identifier, each part's translation record states its recording order in an
optional `part_position` (from 1), added to `archive.migration/translation`
version 1 before release with the operator's approval.

**A segue links to every Work it performs.** One occurrence may carry a
`performance_work` link to each Work the operator names for it. The links
name different Works; they do not compete with each other (INV-7).

**A titled jam may have no Work.** An operator answer of "no Work" leaves the
performance's occurrence, title, and kind in place, and proposes no
`performance_work` link. This is a confirmed absence, reported as such, not an
unresolved row.

Grouping is never inferred from titles such as "part 1" and "part 2". Only an
operator's confirmation groups performances.

## Consequences

The translator reads two additions to the confirmation sheet's answers: a
`works` list for a segue, and a `renditions` list of title groups that form
one rendition. Nothing in the approved contract bundles changes.

## Alternatives considered

- **Merge the parts into one selection.** A recorder split is a rollover, which
  ADR 0048 places in one track of one selection. But that would merge two
  segment chains into one selection identity, and ADR 0047 rule 1 gives each
  chain its own. The review app can propose a merged selection later as a
  native revision.
- **Keep each part as its own occurrence.** This would state one rendition
  twice and split its facts.
