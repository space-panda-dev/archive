# Legacy session backfill

ADR [0124](decisions/0124-legacy-session-backfill.md) governs this explicit
operator procedure. The planner reads the archive through `archive export`;
its output is operational and regenerable. The admitted source snapshot and
translation Claims are durable evidence. Rehearse on scratch and review the
inventory before the operator applies any proposal to archival environments.


Prepare the snapshot mechanically from a retained V2 records copy:

```sh
python -m migration.legacy_inventory RETAINED_RECORDS --output source-inventory
# Optional: include the persisted grouping the legacy UI actually consumed.
python -m migration.legacy_inventory RETAINED_RECORDS \
  --sidecars LEGACY_PREVIEW_CACHE --output source-with-sidecars
```

The adapter reads explicit V2 `session.source_artifact_ids` order. Optional
sidecar mode reads only `*.session.json` metadata, preserving its persisted
ordered `parts` rather than running a recorder grammar. The snapshot embeds
exact source bytes and locators, including tool/version and derived origin;
operator review of this translation does not turn the original grouping into
a human assertion. Sidecar reads are stable per file, not an atomic cache
snapshot; the captured exact bytes and any disagreement remain reviewable.
No original audio is read or copied by sidecar mode. Source records retain
the scanner's frozen-source precondition. Conflicting grouping, source
revision histories, digest mismatches and artifacts without grouping stay
explicit in `inventory.json`; exit 2 means unresolved coverage. No source
rows means no invented sessions. Review the resulting `snapshot.json` and
its inventory before preparing a proposal.

The source snapshot uses JSON format `archive.legacy-sessions/1`.
Its `groups` array contains `id`, `label` and `sources`; each source has `id`,
`primary` (boolean) and ordered `files` with `path` and `digest` (sha256).
Preserve the source's explicit grouping and file order when producing it.
The adapter never infers them from recorder names, listing order or labels.
Each audio digest must already resolve to one current Artifact record. Dates
and ensemble associations remain separate evidence.

The archive must already have the published 0.0.12 contracts. If that
prerequisite is absent, stop and use the fleet upgrade procedure; this command
never upgrades the archive itself. Rehearse with a scratch archive first.

```sh
python -m migration.legacy_backfill snapshot.json \
  --archive "$SCRATCH_ARCHIVE" --archive-command "$ARCHIVE_COMMAND" \
  --operator "$OPERATOR_OBJECT" --door "$DOOR_COMMAND" \
  --staging "$STAGING_DIRECTORY" --output reviewed-proposal
```

The default is dry-run (`--dry-run` is also explicit). It reads the archive,
retains the proposal and checks it with Door without creating staging content,
registering a remote or changing archival state. Output and staging must be
separate directories outside the archive and source snapshot; use a fresh
output directory. A source Artifact is reused when its exact bytes already
have one current Artifact record. Otherwise its deterministic draft joins the
new sessions, parts and translation Claims in one atomic admission. A plan
with no new groups does not admit a source Artifact by itself.

Read `inventory.json` first. Exit 2 means at least one unresolved group;
only unambiguous missing groups have drafts. Missing Artifact records, duplicate
membership or digests, ambiguous primary sources, and conflicting existing
parts need operator review of the source. Never choose the first digest
match or sort files to resolve ambiguity. Matching existing groups remain
unchanged, including named native sessions. The primary source's first digest
establishes the session identity. A missing Artifact record means no current
record resolves that digest; it does not establish that physical audio bytes
are missing. This command does not check physical audio byte availability.

Review `manifest.json`, drafts, `planned-revisions.json`, `inventory.json`
and `context.json`. Door's dry run must return the exact planned revisions.
It does not verify unstaged source content. To apply the retained proposal to
the same scratch archive, repeat the command with the same arguments and add
`--apply`:

```sh
python -m migration.legacy_backfill snapshot.json \
  --archive "$SCRATCH_ARCHIVE" --archive-command "$ARCHIVE_COMMAND" \
  --operator "$OPERATOR_OBJECT" --door "$DOOR_COMMAND" \
  --staging "$STAGING_DIRECTORY" --output reviewed-proposal --apply
```

Apply requires the unchanged snapshot bytes, retained proposal and context.
It checks the exact revision list through Door again before staging source
bytes and admitting the same batch. Record archive HEAD before/after and all
produced revisions. Referenced-head guards protect existing source and audio
pins; a stale guard requires a fresh inventory and review, never removing the
guard. No-op plans write no archival records. Exit 2 remains a partial-result
signal even when the unambiguous groups were admitted successfully.

Repeat the identical apply after a lost response; an unchanged retry leaves
HEAD alone. Replanning into a fresh output directory after admission emits no
records for matching groups, including sessions renamed since admission.
Compare membership and order against the source snapshot and record unresolved
groups. Synthetic real Door tests in `migration/test_legacy_backfill.py` and
`migration/test_legacy_sessions.py` cover atomic source and grouping admission,
exact revisions, guards, provenance targets, partial results and quiet retries.
The operator owns real source review and archival application.

For an already admitted source with known exact pins, the lower-level planner
remains available as `python -m migration.legacy_sessions snapshot.json
--archive ROOT --source-object ID --source-revision REV --operator PERSON
--output PLAN`. It only retains a proposal; the command above handles automatic
source preparation and the retained dry-run/apply lifecycle.
