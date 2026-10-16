# Cutover plan

Status: proposed. This plan authorizes nothing. Every step that touches the
shadow, the real archive, the archive machine, or the V2 system needs its own
operation review and the operator's approval for that step, as the
[shadow runbook](../shadow-runbook.md) requires.

**Cutover** is the moment the V2 system stops importing recordings and Archive
becomes the only way new evidence enters: `capture import` takes the cards,
enrichment and derived media run on what it admits, the relay passes commits
to Door, and the review client writes its decisions as Archive records. Until
then the shadow is read-only to clients, the relay runs `dry-run`
([ADR 0056](../decisions/0056-tailnet-relay.md)), and new rehearsals enter the
V2 system or, with approval per run, the shadow
([ADR 0070](../decisions/0070-capture-import.md)).

This plan lists what must be true first, one choice ADR 0070 leaves to it, the
order of the steps, how to roll back, and where Archive stands against the V2
system's capabilities.

## What must be true before cutover

Each condition names its state after the merge of specification 0.0.7, the
specification upgrade, index repair, World projection 6 revision 3, the
probe, the span detector, the work matcher, derived media, and the capture
importer.

1. **The real archive is chosen and named.** The shadow is a disposable,
   faithful migration of the frozen V2 bundle; scratch copies are never
   evidence ([ADR 0061](../decisions/0061-scratch-archives.md)). No decision
   yet says which archive becomes the real one: the shadow, promoted after a
   final review, or a fresh migration of the same frozen bundle (ADR 0036
   fixes that import's identity, so both carry the same migrated records).
   Recordings imported natively into the shadow beforehand would have to be
   imported again into a fresh migration, which ADR 0070 makes safe. *Open:
   needs the operator's decision.*
2. **The real archive is at 0.0.7.** Derivation runs, stream facts, span
   suggestions, and version 2 matches exist only from 0.0.7 on. A 0.0.6
   archive adopts it by one `door transition`
   ([ADR 0068](../decisions/0068-specification-upgrade.md)); a fresh
   migration can be born at 0.0.7. *Ready in code:* Door applies the upgrade
   in one commit and refuses every non-additive or forged one, and World's
   next refresh rebuilds in full and conforms
   ([`test_upgrade.py`](../../world/tests/test_upgrade.py)). *Not done:* the
   [runbook's upgrade](../shadow-runbook.md#specification-upgrade) has not
   run on a scratch copy of the shadow or on the shadow.
3. **The deployed build is this one.** The deployed relay and Door predate
   ADRs 0064-0070 and cannot apply an upgrade. *Not done:* the
   [redeploy](../shadow-runbook.md#redeploying-door-world-and-the-relay)
   needs approval, and it must precede the upgrade.
4. **The importer is in place.** *Ready in code:* `capture import` hashes a
   card, skips known content, groups sessions and sources with the Zoom
   H4essential grammar (including rollover), and admits one content-bearing
   batch per session through a git-annex staging repository; a second import
   changes nothing. *Not done:* a staging repository and a derived-media root
   on the archive machine, the [import procedure](../shadow-runbook.md#importing-new-recordings)
   rehearsed on a scratch copy and then on the shadow, and a recorder grammar
   for any other recorder in use.
5. **Enrichment and derived media run on every import.** *Ready in code:*
   the importer runs `enrich probe`, `enrich detect` on the primary source,
   and `enrich/media.py` on each new Artifact. *Not done:* the detector's
   `jam` and `show` presets are V2's values on new signals and have been
   tested only on synthetic audio, so they need calibrating on real
   recordings; the importer always uses `jam`. The work matcher is not in the
   chain: `enrich match` is an operator command, and its `--limit` has no
   default because its scores are not on the V2 scale. Calibrating it on known
   same-song and different-song pairs is the operator's step before any match
   is trusted. A chain that stops part way is finished by hand; a rerun of the
   import does not resume it.
6. **The review client commits.** The client's decision contracts are
   installed ([ADR 0048](../decisions/0048-review-app-write-contracts.md)) and
   Door admits them. *Not done (client side):* the client reads the shadow
   through the relay and sends dry runs only; it must write drafts under the
   archive's own `spec`, which Door now enforces, read peaks and listening
   copies from the relay's media routes instead of the V2 streams, and show
   span suggestions and matches from World. Its commit path is exercised
   against a scratch relay (role `scratch`) first.
7. **The relay commits.** A relay for the real archive runs with `role`
   `live` and `writes` `commit`, refreshes World after each commit, and has a
   `media_root` and callers bound to the operator's person
   ([ADR 0060](../decisions/0060-relay-interface-and-role.md)). Switching to
   `commit` is its own approval (ADR 0056). *Not done.*
8. **Publication has a path.** Approval, hold-back, and withdrawal are
   transitions Door commits
   ([ADR 0019](../decisions/0019-publication-approval-is-a-transition.md),
   [ADR 0051](../decisions/0051-publication-transition-dispatch.md)). The act
   of publishing (placing a copy where listeners reach it, with its
   disclosure recorded first) is not built; hosted placement is only proposed
   ([ADR 0039](../decisions/0039-proposed-hosted-plaintext-placement.md)).
   *Not done:* either Archive's publication path exists at cutover, or the
   V2 system keeps serving what it already published, read-only, and nothing
   new is published until Archive's path exists. The second is the smaller
   step and loses nothing; it is recommended unless publication is needed at
   once.
9. **Backups cover the new state.** The staging repository and the
   derived-media root are operational and regenerable respectively
   ([state model](../state-model.md)); the archive itself, with its annexed
   content, is evidence and must be inside the backup scope before the first
   card is imported into it and wiped. *Not done.*
10. **The stale gate text is settled.** Specification D-4 still makes
    content-bearing admission unsupported "until the required INV-5 git-annex
    experiment has been run and its result accepted"; the operator accepted
    the real-content run for content admission (see the
    [open questions](../open-questions.md#door)), and Door, the fixture
    builder, enrichment, and the importer all commit such batches. A later
    specification release should drop the condition rather than leave it to
    be read as still open. *Not done; does not block.*

## Reviews made in the V2 system after the frozen bundle

The shadow and any migration of the frozen bundle hold only what the bundle
held. Recordings made since then are in the V2 system, and some have been
reviewed there. ADR 0070 does not migrate those reviews and leaves this plan to
choose ([ADR 0036](../decisions/0036-v2-frozen-migration-bundle.md) requires a
separately accepted identity and accounting rule for any later V2 source).

- **A. Review them again in the new client.** Import the recordings natively
  from their original files with `capture import`, and redo their review in
  the new client after cutover. The V2 reviews stay in the V2 system, readable,
  as notes for the person re-reviewing. Nothing new is decided; the cost is
  repeating the reviews of the recordings since the bundle, and the earlier
  reviews' dates and authorship are not carried.
- **B. Accept a later-import rule.** Write and accept a decision that gives
  V2 records made after the frozen bundle an identity and accounting rule, and
  migrate their reviews as ADR 0047's route does for the bundle. The reviews
  survive with their history; the cost is a second migration route to design,
  test, and keep, for a bounded number of records, and recordings imported
  natively would have to be reconciled with the migrated reviews of the same
  content.

**Recommendation: A**, with the freeze moved as close to now as possible: stop
reviewing post-bundle recordings in the V2 system, import them into the
shadow (with approval) so the client can show them, and review them once the
client commits. The number of such recordings is fixed once V2 stops
importing, and B would add a migration route that serves only this gap. Choose B only if those reviews carry
judgments that cannot be made again.

## Order

Each step is separately approved and leaves the previous state recoverable.

1. Redeploy Door, World, and the relay on the shadow, still `dry-run`
   ([runbook](../shadow-runbook.md#redeploying-door-world-and-the-relay)).
2. Rehearse the specification upgrade on a scratch copy of the shadow, then
   upgrade the shadow ([runbook](../shadow-runbook.md#specification-upgrade)).
3. Rehearse `capture import` on a scratch copy with a real card, then import
   the post-bundle recordings into the shadow. Calibrate the detector presets
   and the matcher's limit on the shadow's real recordings.
4. Exercise the review client's commits against a scratch relay.
5. Decide which archive is the real one (condition 1), and bring it to 0.0.7.
6. Put the real archive, its staging repository, and its derived-media root
   inside the backup scope.
7. Stop the V2 import, start importing into the real archive, and switch its
   relay to `live` and `commit`.
8. Publication stays in the V2 system, read-only, until Archive's path exists
   (condition 8).

## Rollback

- **Before the first commit to the real archive** (steps 1-6), rollback is
  restoring the previous unit files and builds, or the snapshot taken before
  an upgrade. The V2 system is still the importer and nothing is lost.
- **After it**, rollback means switching the relay back to `dry-run` and
  resuming the V2 import. Archive records are never removed: what was
  committed stays, and recordings imported in the meantime are already
  Artifacts there. They must also be brought into the V2 system (from the
  same cards, which the operator keeps until their content is backed up) if
  V2 is to stay complete. Rollback past an upgrade is the pre-upgrade
  snapshot, which discards everything committed after it, so the upgrade
  comes before any commit that matters.

## Parity with the V2 system

| V2 capability | Archive after this work | Status |
|---|---|---|
| Card import | `capture import`: hashing, known-content skip, recorder grammar with rollover, one content-bearing Door batch per session (ADR 0070) | Ported; one recorder grammar |
| FLAC listening files and clips | Not made. Listening copies are AAC-LC in MP4 (ADR 0069); no FLAC derivative or shared-folder view exists | Not ported; decide whether still wanted |
| Technical metadata | `enrich probe`: exact decoded sample count, rate, channels, codec per audio stream, as `audio_stream` claims under a derivation run (ADR 0065) | Ported for audio streams; other file metadata not claimed |
| Purpose labels | The label contract is deferred (open representation and current rule). The span detector classes each suggested span `music`, `talk`, `quiet`, or `unknown` | Not ported; span classes only |
| Boundary detection | `enrich detect`: span suggestions in integer samples from silence, loudness, and chroma novelty (ADR 0066) | Ported; presets uncalibrated |
| Feature timeline | Not a claim (contract deferred). Waveform peaks every 10 ms cover drawing the recording (ADR 0069) | Not ported; drawing covered by peaks |
| Key and tempo analysis | Not ported; the musical-analysis contract is deferred | Not ported |
| Transcription | Not ported; the transcript contract and its disclosure adapter are open | Not ported |
| Cover matching | `enrich match`: `work_match` version 2 under a run, its own chroma cross-recurrence method, scores not on V2's scale (ADR 0067) | Ported; limit uncalibrated, operator-run |
| Listening streams and clips | Relay `GET /media/{digest}/listen` and `/peaks`, whole recording, byte ranges, operator only (ADR 0069); no per-span clips | Ported for whole recordings |
| Review and apply | Review contracts installed and admitted (ADR 0048); relay passes commits in `commit` mode; client sends dry runs only | In progress (client and approval) |
| Session naming | The importer groups files into a `recording_session` with one parts claim per source, labelled by recorder and clock; renaming is a client revision | Ported (grouping); naming through the client |
| Publication | Approval, hold-back, and withdrawal transitions (ADRs 0019, 0051); no publishing path | Not ported beyond approval |
| Read tools | World over SPARQL, the relay's `/world/query`, `archive ls`, `show`, and `export`, and the recording examples; no agent tool server | Partly ported |
