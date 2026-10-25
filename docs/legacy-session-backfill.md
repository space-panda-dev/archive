# Legacy session backfill

ADR [0121](decisions/0121-legacy-session-backfill.md) governs this explicit
operator procedure. The planner reads the archive through `archive export`;
its output is operational and regenerable. The admitted source snapshot and
translation Claims are durable evidence. Rehearse on scratch and review the
inventory before the operator applies any proposal to archival environments.

The admitted source snapshot uses JSON format `archive.legacy-sessions/1`.
Its `groups` array contains `id`, `label` and `sources`; each source has `id`,
`primary` (boolean) and ordered `files` with `path` and `digest` (sha256).
Preserve the source's explicit grouping and file order when producing it.
The adapter never infers them from recorder names, listing order or labels.
Review and admit that snapshot using the Artifact admission process first;
keep its exact object/revision pin and verify its digest against the file.
Each audio digest must already resolve to one current Artifact. Dates and
ensemble associations remain separate evidence.

```sh
python -m migration.legacy_sessions snapshot.json \
  --archive "$SCRATCH_ARCHIVE" --archive-command "$ARCHIVE_COMMAND" \
  --source-object "$SOURCE_OBJECT" --source-revision "$SOURCE_REVISION" \
  --operator "$OPERATOR_OBJECT" --output reviewed-proposal
```

Read `inventory.json` first. Exit 2 means at least one unresolved group;
only unambiguous missing groups have drafts. Missing content, duplicate
membership or digests, ambiguous primary sources, and conflicting existing
parts need operator review of the source. Never choose the first digest
match or sort files to resolve ambiguity. Matching existing groups remain
unchanged, including named native sessions. The primary source's first digest
establishes the session identity.

Review `manifest.json`, drafts and `planned-revisions.json`. Do not submit an
empty proposal: it represents an unchanged run. Dry-run the reviewed batch
through Door and compare its complete revision list to the planned list;
then the operator admits the identical batch against scratch. Record archive
HEAD before/after and all produced revisions. Atomic referenced-head guards
protect the source and audio pins. A stale guard requires a fresh inventory
and review; never remove the guard.

Repeat the identical admission after a lost response; unchanged retry must
leave HEAD alone. Run the planner into a new output directory: matching
admitted groupings must emit no records. Compare membership and order against
the source snapshot and record unresolved groups. The real Door test in
`migration/test_legacy_sessions.py` rehearses exact revisions, provenance
target refusal, identical retry and a second no-op plan with synthetic
content. The operator owns real source review and archival application.
