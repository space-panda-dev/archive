# Review client: World metadata and existing sessions

Status: proposed implementation and proof scope (2026-10-25). This document
accepts no new meaning and authorizes no deployment or archive mutation.
See [ADR 0123](../decisions/0123-session-ensemble-association.md) for the
accepted ensemble association and [ADR 0107](../decisions/0107-multi-device-and-live.md)
for accepted capture semantics.

## Gate and dependency order

The review client must use one World reader for old and new recordings,
without a legacy preview index or lead-sheet interface. Its metadata gate
requires archival date and ensemble evidence, or a precise unavailable
answer. Session naming is already an Entity revision (ADR 0048); adding a
name must never regroup or reorder recordings.

1. **Inventory through the published reader.** Use `wf observe` to identify
   the serving build and role, then World queries for Artifacts, session
   Entities and ordered `session_parts` references. Report Artifacts with no
   session, multiple sessions or missing primary-source coverage explicitly.
   Do not infer session existence from known content, and do not read a
   sibling application's private index to fill holes.
2. **Release accepted date evidence.** Implement ADR 0107's `capture_start`
   version 1 as an additive specification release: schema and profile rules,
   content-addressed bundle, catalog, context, Door validation, fixture and
   World projection. Keep local wall time distinct from zoned time; an absent
   `started` is unknown, never Door's `at`. A capture tool asserts what its
   grammar/header read with its own software provenance. A person's clock
   correction coexists as another assertion. Cover malformed dates, clock
   pairing, unknown start, local/zoned starts and competing assertions with
   Door and World agreement tests. Preserve every old contract bundle.
3. **Read session dates by the accepted rule.** Use the primary source's
   first part. Several starts, or no start with `started`, yield unavailable
   with a reason (ADR 0107); do not parse a session label. Extend the
   permission-aware named view, so members need no raw SPARQL. A recording
   with no session must remain explicitly outside this rule.
4. **Implement accepted source joining.** Release `session_parts` version 2
   with its required source description, `capture import --into` and the
   parts-only `capture parts --into` path from ADR 0107. Version 1 remains
   readable. These attach sources to an existing session on the operator's
   explicit instruction; capture clocks are hints, never automatic joins.
5. **Implement accepted missing-session backfill separately.** ADR 0070's
   amendment already adopts known-but-unparented Artifacts into sessions
   using recorder grammar grouping. That does not preserve a distinct
   legacy session grouping or its source provenance. ADR 0107's parts-only
   path requires an existing session. [ADR 0124](../decisions/0124-legacy-session-backfill.md) requires the
   legacy grouping and file order, source provenance and explicit review of
   ambiguous groups before proposing those records. Follow ADR 0048's first-part identity once the grouping is
   established. Do not make a changed label or a repeat import perform this
   migration implicitly. [Open questions](../open-questions.md) tracks this
   boundary.
6. **Release the accepted ensemble decision.** Follow ADR 0123;
   neither client config nor a membership-derived band satisfies the gate.

The initial 0.0.11 code inspection found neither `capture_start` in its
catalog nor `--into`/`parts` in the capture CLI. Recheck those facts in the
chosen implementation worktree and serving build before execution. These
are cross-layer release steps, not client-only fallback changes.

## Repeat-card proof for the operator

Prepare actual paths from observed state. The card is mounted read-only.
Use a scratch copy of the relevant archive, with its own staging repository
and derived-media root, and confirm its relay role is `scratch` and writes
`commit`. Use [the scratch procedure](../shadow-runbook.md#scratch-archives);
never point a commit at the shadow relay.

```sh
capture import --archive "$SCRATCH/archive" --staging "$SCRATCH/capture-staging" \
  --derived-media "$SCRATCH/derived-media" "$CARD" --dry-run
capture import --archive "$SCRATCH/archive" --staging "$SCRATCH/capture-staging" \
  --derived-media "$SCRATCH/derived-media" "$CARD"
```

Record the scratch archive HEAD before and after, both JSON answers, exit
statuses, each session's status and any refused partially known group. Keep
reports outside the archive. With every card file already belonging to a session, expect
`unchanged`, `already_imported` groups and identical HEAD. Known content with
no session may instead produce a content-free adoption under ADR 0070; review
the predicted grammar grouping before allowing that scratch run. This proves no-op
retry; it does not prove a new session, missing-session backfill, date
backfill or enrichment completion. An unchanged import does not resume a
stopped enrichment chain (see the existing open question).

Prove new admission separately with synthetic recorder files in an archive
where their content is absent: predicted revisions equal admitted revisions,
one session per grammar grouping, named session for unrecognised files,
correct rollover order, explicit source join, a second import writes
nothing, and a partial-known group refuses before admission. Exercise
parts-only joining with existing synthetic Artifacts after its release.
Use the same naming/read path for a native session and a deliberately
backfilled session once its procedure is accepted.

## Release evidence

For each implementation commit run `wf check --tier 30 --changed`, `wf vet`
and the workflow budget checkpoint; before its PR run tier 300. Contract
and projection changes need real Door/World fixture agreement, not a client
prediction alone. Prepare the operator's observed-build, scratch and two-login
steps with expected outcomes; only the operator merges, deploys and performs
real-login checks. No command above authorizes starting or restarting services.
