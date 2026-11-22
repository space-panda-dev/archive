# Cutover plan

Status: proposed. This plan authorizes nothing. Every step that touches the
shadow, the real archive, the archive machine, or the V2 system needs its own
operation review and the operator's approval for that step, as the
[shadow runbook](../shadow-runbook.md) requires.

**Cutover** is the moment the V2 system stops importing recordings and Archive
becomes the only way new evidence enters: `capture import` takes the cards,
enrichment and derived media run on what it admits, the relay passes commits
to Door, and the review client writes its decisions as Archive records. Until
then the relay runs `dry-run` ([ADR 0056](../decisions/0056-tailnet-relay.md)),
and new rehearsals enter the V2 system and, with approval per run, the shadow
([ADR 0070](../decisions/0070-capture-import.md)).

This plan lists what must be true first, the order of the remaining steps, how
to roll back, and where Archive stands against the V2 system's capabilities.
It was last refreshed for specification 0.0.12; a first go/no-go was held and
answered no, and no new date is set.

## What must be true before cutover

Each condition says how it is checked, so that its state can be read from the
archive machine, the relay, and this repository rather than from memory. A
condition that only a person can settle says so.

1. **The real archive is chosen.** *Decided:* the shadow, a faithful migration
   of the frozen V2 bundle, is promoted to be the real archive rather than
   migrating the bundle again ([ADR 0061](../decisions/0061-scratch-archives.md)
   keeps scratch copies out of the evidence). Recordings already imported
   natively into the shadow stay. *Settled by the operator.*
2. **The real archive is at the current specification.** *Done:* the shadow
   was upgraded step by step to 0.0.12, each by one `door transition`
   ([ADR 0068](../decisions/0068-specification-upgrade.md)); later upgrades
   are applied as pinned plans ([ADR 0126](../decisions/0126-fleet-specification-upgrade-boundary.md)).
   *Checked:* the archive's own `spec` equals `spec/VERSION` on main.
3. **The deployed build is current.** Door, World and the relay run a build
   from main after 0.0.12. *Not done:* one later change to World conformance
   time and memory is not deployed; the redeploy is a pinned plan the operator
   applies. *Checked:* the relay's commit
   ([ADR 0118](../decisions/0118-relay-metrics.md)) against main, counting only
   changes to code.
4. **The importer is in place.** *Done:* `capture import` was rehearsed on a
   scratch copy with real cards and then run on the shadow. It keeps one file
   per take where the recorder writes a mix and a microphone twin, and sessions
   adopt migrated parts that had none (ADR 0070 as amended). The staging
   repository and derived-media root exist on the archive machine. One
   recorder grammar is supported; another recorder needs its own.
5. **Enrichment runs on every admission.** *Done:* enrichment runs as a
   service over what is admitted ([ADR 0120](../decisions/0120-internal-enrichment-composition.md)),
   including source separation on another machine through a work spool, and
   every recording in the shadow has its results. *Decided:* the detector's
   presets and the work matcher's scores stay suggestions; uncalibrated
   thresholds do not block cutover. *Checked:* the service is active and
   logging, and the spool has no long-waiting jobs.
6. **The review client commits.** *Done in the client:* it saves reviews,
   session names and credits through the relay, reads recordings, peaks and
   listening copies from the relay instead of the V2 streams, and takes dates
   and ensembles from World. Its commit path passes its end-to-end suite
   against a scratch relay, and that build is deployed. *Not done:* the
   operator's own sign-in, saving against a scratch relay (role `scratch`),
   which is the only proof with a real identity
   ([ADR 0088](../decisions/0088-bandmates.md)). *Settled by the operator.*
7. **Migrated recordings have sessions.** Recordings migrated without a
   session get one from the V2 grouping and file order
   ([ADR 0124](../decisions/0124-legacy-session-backfill.md)). *Done:* the
   operator decided the ambiguous groups, and the packaged command's source
   is prepared. *Not done:* its dry run on the shadow, the operator's review of
   it, and the apply. *Checked:* session records admitted by the backfill.
8. **The real archive has everything the V2 system has.** *Done:* every
   recording in the V2 system is in the shadow, one mix per take. *Checked:*
   no recording entered the V2 system after the shadow was last completed;
   checked again on the day.
9. **Backups cover the new state.** The archive, with its annexed content, is
   evidence and must be inside the backup scope before the first card is
   imported into it and wiped. *Done in the interim:* the existing nightly
   offsite restic backup lists the archive, its staging repositories and the
   feature bank. Most annexed content is byte-identical to content that backup
   already holds, so it adds little. World's store and derived media are
   regenerable ([state model](../state-model.md)) and left out. A backup
   declared from contracts' state replaces this list later. *Checked:* the
   list names the archive and the last run succeeded after it did.
10. **Publication has a path.** Approval, hold-back, and withdrawal are
    transitions Door commits
    ([ADR 0019](../decisions/0019-publication-approval-is-a-transition.md),
    [ADR 0051](../decisions/0051-publication-transition-dispatch.md)); the act
    of publishing is not built, and hosted placement is only proposed
    ([ADR 0039](../decisions/0039-proposed-hosted-plaintext-placement.md)).
    *Recommended:* the V2 system keeps serving what it already published,
    read-only, and nothing new is published until Archive's path exists. This
    loses nothing. *Settled by the operator.*
11. **The relay commits.** A relay for the real archive runs with `role`
    `live` and `writes` `commit`, refreshes World after each commit, and has a
    `media_root` and callers bound to the operator's person
    ([ADR 0060](../decisions/0060-relay-interface-and-role.md)). Switching is
    its own approval (ADR 0056) and happens on the day. *Checked:* the relay's
    role and writes.
12. **The stale gate text is settled.** Specification D-4 still makes
    content-bearing admission unsupported "until the required INV-5 git-annex
    experiment has been run and its result accepted"; the operator accepted
    the real-content run (see the [open questions](../open-questions.md#door)),
    and Door, enrichment, and the importer all commit such batches. A later
    specification release should drop the condition. *Not done; does not
    block.* *Checked:* the sentence on main.

## Reviews made in the V2 system after the frozen bundle

*Decided: option A.* No reviews are made in the V2 system after the decision;
recordings since the bundle are imported natively and reviewed in the new
client. The V2 reviews stay in the V2 system, readable, as notes for the
person re-reviewing; their dates and authorship are not carried. The
alternative, a later-import rule with a second migration route
([ADR 0036](../decisions/0036-v2-frozen-migration-bundle.md)), was not chosen:
it would serve only this bounded gap.

## Order

Each step is separately approved and leaves the previous state recoverable.
Steps 1-5 can run in any order; 6 waits for all of them.

1. Redeploy the current build on the shadow, still `dry-run` (condition 3).
2. The operator's sign-in test of saving against a scratch relay (condition 6).
3. Dry-run the legacy session backfill on the shadow, review it, then apply it
   (condition 7).
4. Confirm the first nightly backup with the archive in scope (condition 9).
5. Accept this plan, its rollback, and the publication choice (condition 10).
6. Set the go/no-go date. On the day: confirm nothing new entered the V2 system
   (condition 8); stop the V2 import, keeping the V2 system installed for
   rollback; switch the relay to `live` and `commit` (condition 11); import the
   first card into the real archive, and wipe it only once a backup holds it.
7. Publication stays in the V2 system, read-only, until Archive's path exists.

## Rollback

- **Before the first commit to the real archive**, rollback is restoring the
  previous unit files and builds. The V2 system is still the importer and
  nothing is lost.
- **After it**, rollback means switching the relay back to `dry-run` and
  resuming the V2 import. Archive records are never removed: what was
  committed stays, and recordings imported in the meantime are already
  Artifacts there. They must also be brought into the V2 system (from the
  same cards, which the operator keeps until their content is backed up) if
  V2 is to stay complete.
- **Past a specification upgrade**, rollback is the pre-upgrade snapshot,
  which discards everything committed after it, so any further upgrade comes
  before the commits that matter.

## Parity with the V2 system

| V2 capability | Archive at cutover | Status |
|---|---|---|
| Card import | `capture import`: hashing, known-content skip, recorder grammar with rollover, one file per take, one content-bearing Door batch per session (ADR 0070) | Ported; one recorder grammar |
| FLAC listening files and clips | Not made. Listening copies are AAC-LC in MP4 (ADR 0069); no FLAC derivative or shared-folder view exists | Not ported; after cutover |
| Technical metadata | `enrich probe`: exact decoded sample count, rate, channels, codec per audio stream (ADR 0065) | Ported for audio streams |
| Purpose labels | The span detector classes spans `music`, `talk`, `quiet`, or `unknown`, as suggestions | Span classes only |
| Boundary detection | `enrich detect`: span suggestions in integer samples (ADR 0066) | Ported; presets advisory |
| Musical analysis | Enrichment: stems, chords from two recognisers, beats, downbeats and tempo, structure, pitch and melody, for every recording (ADR 0120) | Ported, beyond V2 |
| Transcription | Shape decided (ADR 0104); not run at cutover | After cutover |
| Cover matching | `enrich match`: `work_match` version 2, scores not on V2's scale (ADR 0067) | Ported; scores advisory |
| Listening streams and clips | Relay `GET /media/{digest}/listen` and `/peaks`, whole recording, byte ranges (ADR 0069); no per-span clips | Ported for whole recordings |
| Review and apply | Review contracts admitted (ADR 0048); the client saves through the relay | Ported; commits from the day the relay goes live |
| Session naming and credits | The importer groups files into a `recording_session`; names and credits are client revisions | Ported |
| Publication | Approval, hold-back, and withdrawal transitions (ADRs 0019, 0051); no publishing path | After cutover |
| Read tools | World over SPARQL, the relay's `/world/query`, `archive ls`, `show`, and `export`; no agent tool server | Partly ported |
