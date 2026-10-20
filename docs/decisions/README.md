# Decisions

These records explain why the architecture is the way it is. Each states a
decision, the reason it needed deciding, and the alternatives it rejected. The
rejected alternatives matter most: they stop the same question from being
reopened without new information.

The set was consolidated before the first release, so each record describes a
settled theme rather than one step of the discussion that produced it. The
earlier, step-by-step records remain in Git history.

To change a decision, add a new record that supersedes the old one and mark the
old one `Status: superseded by NNNN`; do not rewrite accepted records. Undecided
matters belong in [open-questions.md](../open-questions.md), not here.

| # | Decision |
|---|---|
| [0001](0001-archive-preserves-evidence.md) | Archive preserves evidence, is curated, and stands alone |
| [0002](0002-one-door.md) | One Door governs archival meaning |
| [0003](0003-record-model.md) | The record model |
| [0004](0004-deletion.md) | Deletion: retire and destroy |
| [0005](0005-claims-review-and-authority.md) | Claims, review, and two kinds of authority |
| [0006](0006-enrichment.md) | Enrichment derives without mutating, and stays reproducible |
| [0007](0007-policy.md) | Policy declares desired state (amended 2026-10-19 by 0105: the snapshot desire) |
| [0008](0008-plan-and-apply.md) | Plan, then apply; history stays quiet |
| [0009](0009-copies-and-custody.md) | Copies, custody, and storage |
| [0010](0010-publication.md) | Publication: destinations, approval, and rights |
| [0011](0011-world.md) | World is a projection |
| [0012](0012-inherited-meaning.md) | Inherit meaning; keep Archive's contracts |
| [0013](0013-physical-collections.md) | Physical collections |
| [0014](0014-boundaries-and-languages.md) | Spool, languages, and repository boundaries |
| [0015](0015-porting-from-v2.md) | Porting from the V2 spike by translation |
| [0016](0016-disclosure.md) | Disclosure: content leaving the operator's custody |
| [0017](0017-licensing.md) | Licensing: copyleft code, public-domain contracts |
| [0018](0018-people-in-the-archive.md) | People in the archive who are not its operator (amended 2026-10-19 by 0096: depositors' limits) |
| [0019](0019-publication-approval-is-a-transition.md) | Publication approval is a transition claim |
| [0020](0020-archive-on-disk-layout.md) | The archive on-disk layout |
| [0021](0021-portable-identifiers.md) | Portable archive and object identifiers |
| [0022](0022-normative-record-envelope.md) | The normative record envelope |
| [0023](0023-admission-and-staging.md) | Admission uses metadata proposals and git-annex staging |
| [0024](0024-retire-and-destroy-records.md) | Retire and destroy records keep decisions, not execution state (note 2026-10-19: lineage and containment dependents, 0098 and 0101) |
| [0025](0025-versioned-json-ld-contexts.md) | JSON-LD contexts are local and versioned with their contracts |
| [0026](0026-initialization-and-identity-allocation.md) | Initialization bootstraps one archive and one operator |
| [0027](0027-core-payloads-and-installed-contracts.md) | Core payloads are closed; kind payloads use installed contracts |
| [0028](0028-transition-proposals-and-dispatch.md) | Door dispatches atomic transition proposals by exact contract |
| [0029](0029-core-context-and-bootstrap-contract.md) | The core context and bootstrap contract are exact local resources |
| [0030](0030-durable-transition-order.md) | Transition batches retain their authorization order |
| [0031](0031-power-loss-durability.md) | Door acknowledges only durable commits |
| [0032](0032-managed-placement-over-ssh.md) | Managed placement names a location, not a host |
| [0033](0033-operator-configured-executables.md) | Git executable paths are operator configuration |
| [0034](0034-git-object-store-coordination.md) | Coordinate Git object-store maintenance with Door |
| [0035](0035-v2-migration-preserves-evidence-and-claims.md) | V2 migration preserves source evidence and admits supported claims |
| [0036](0036-v2-frozen-migration-bundle.md) | The first V2 migration is one frozen, accounted bundle |
| [0037 proposed](0037-proposed-private-managed-copy.md) | Whole-Archive copy scope |
| [0038 proposed](0038-proposed-private-placement-protocol.md) | Placement configuration and wire protocol |
| [0039 proposed](0039-proposed-hosted-plaintext-placement.md) | Hosted plaintext managed placement and authenticated seedbox projections |
| [0040](0040-audio-section-song-route.md) | V2 audio-section-to-song claim route |
| [0041](0041-world-v1-projection.md) | World v1 builds a disposable, provenance-preserving graph |
| [0042](0042-work-correspondence.md) | A work correspondence is an attributable mapping claim |
| [0043](0043-geographic-evidence-and-route-handoff.md) | Geographic observations and route handoff |
| [0044](0044-audio-selections-and-song-occurrences.md) | Audio selections and song occurrences have separate identities |
| [0045](0045-audio-claim-contracts.md) | Audio claims bind exact selections to occurrences |
| [0046](0046-world-release-before-v2-migration.md) | Sequence World contracts before the V2 migration route |
| [0047](0047-v2-audio-route-source-rules.md) | Source translation rules for the V2 audio route |
| [0048](0048-review-app-write-contracts.md) | Review application write contracts |
| [0049](0049-v2-migration-route-contracts.md) | V2 migration route contracts |
| [0050](0050-operator-confirmed-renditions.md) | Operator-confirmed renditions in the V2 migration |
| [0051](0051-publication-transition-dispatch.md) | Door dispatches publication transitions |
| [0052](0052-incremental-admission-verification.md) | Door verifies admissions incrementally from its own checkpoint |
| [0053](0053-fanned-out-record-and-content-paths.md) | Layout archive/2 fans out record and content paths |
| [0054](0054-door-service-for-applications.md) | A long-running Door service for applications |
| [0055](0055-incremental-world-refresh.md) | World refreshes incrementally |
| [0056](0056-tailnet-relay.md) | A tailnet relay for the operator's browser (amended 2026-10-19 by 0092: an internet listener) |
| [0057](0057-specification-contract-catalogs.md) | Each specification version lists its contracts |
| [0058](0058-door-refusal-codes.md) | Door's refusals carry stable codes |
| [0059](0059-world-read-contract.md) | World publishes a versioned read contract |
| [0060](0060-relay-interface-and-role.md) | The relay publishes its interface and its role |
| [0061](0061-scratch-archives.md) | Scratch archives for testing real commits |
| [0062](0062-release-bundle-and-ci.md) | Releases carry an image and a bundle, and CI checks both |
| [0063](0063-relay-read-speed.md) | Relay reads at interactive speed: cache, concurrency, and gzip |
| [0064](0064-derivation-runs.md) | Derivation runs are claims, and enrichment code lives in `enrich/` |
| [0065](0065-audio-stream-facts.md) | Audio stream facts are machine claims about an Artifact |
| [0066](0066-span-suggestions.md) | Machine-suggested spans are admitted claims |
| [0067](0067-matches-and-alignments-pin-runs.md) | Matches and alignments pin a derivation run |
| [0068](0068-specification-upgrade.md) | An archive upgrades its specification by a transition |
| [0069](0069-derived-media.md) | Waveform peaks and listening copies are derived media, served by digest |
| [0070](0070-capture-import.md) | New recordings are imported natively |
| [0071](0071-essentia-cover-similarity.md) | Cover similarity is measured with Essentia, as the V2 system calibrated it (amended 2026-10-18, accepted) |
| [0072](0072-windowed-work-matches.md) | A work match can suggest a window of a recording (amended 2026-10-18, accepted) |
| [0073](0073-feature-bank.md) | A feature bank holds regenerable features, keyed by content and method |
| [0074](0074-extractor-contract.md) | An extractor is any program with a manifest, run by request (amended 2026-10-19, accepted) |
| [0075](0075-stems-as-content.md) | Stems are separated outside the archive's environment, and a stem is content to every extractor |
| [0076](0076-preferred-inputs.md) | An extractor may prefer a file feature of its content as its default input (accepted 2026-10-19, amended: a mostly unvoiced stem falls back) |
| [0077](0077-chord-stacks-and-meter.md) | Chords are stacks and meter is a timeline, each asserted by someone |
| [0078](0078-contributions.md) | A contribution names who did what, in which role, to how much of the music |
| [0079](0079-machine-harmony-claims.md) | Machine harmony becomes claims; stems are cited by bank key |
| [0080](0080-key-and-tuning.md) | Key regions and tuning are claims |
| [0081](0081-judgments-and-annotations.md) | Judgments can scope to a span or reading; people can annotate spans |
| [0082](0082-views-and-current.md) | Named views: the first current rule, correction overlay, differences, and chord names over the relay |
| [0083](0083-form-and-work-meter.md) | Form is a claim, and a Work can have meter without a recording |
| [0084](0084-lead-sheets.md) | A lead sheet is a claim that pins the parts it chose |
| [0085](0085-documents-lyrics-rights.md) | Documents arrive through the relay; lyrics are timed by reference; sensitivity and rights are claims (amended 2026-10-19: syllables and their notes; Door checks an approval beyond the circle, World's `may_publish`; by 0092: the "circle" setting writes `GREEN`) |
| [0086](0086-notes-and-melody.md) | Note events per part, and melody at two levels (amended 2026-10-19: articulation, written rhythm, the bass line) |
| [0087](0087-tracklists-and-correspondence.md) | Releases list their tracks; artists, releases and recordings correspond to MusicBrainz and Discogs |
| [0088](0088-bandmates.md) | Bandmates read their circle and assert their own claims (amended 2026-10-19 by 0092, 0093 and 0094: internet members, audiences, members' writes) |
| [0089](0089-practice-audio-and-exports.md) | Practice audio from the stems, transposition, and exports that are views until shared |
| [0090](0090-extractor-runs-travel-through-spool.md) | An extractor run travels through Spool, and its files come back as result attachments |
| [0091](0091-talk-needs-level.md) | Talk needs level, and a studio preset never says talk (amends 0066's presets) |
| [0092](0092-internet-exposure.md) | Internet exposure through Cloudflare Access; internet logins are members |
| [0093](0093-audiences.md) | A relay has several named audiences |
| [0094](0094-peoples-writes.md) | People's writes: grants are transitions, and members set their own limits |
| [0095](0095-participation.md) | Participation is a contribution at session scope, and only the operator counts it |
| [0096](0096-origin-of-recordings.md) | Recordings and Artifacts have an origin |
| [0097](0097-rights-by-use.md) | Rights say which use they permit, and a clearance permits one use |
| [0098](0098-content-lineage.md) | Content lineage: an Artifact can say what it was made from |
| [0099](0099-samples.md) | A sample is a selection with a stem, a recipe and kinds, rendered on demand |
| [0100](0100-packs-and-collections.md) | Sample packs are releases, libraries are collections, and source paths are kept |
| [0101](0101-daw-projects.md) | A DAW project is an Entity with snapshots, its audio, and lineage to its bounces |
| [0102](0102-retrieval-index.md) | World keeps a second disposable store for facets, embeddings and text search |
| [0103](0103-voice-capture.md) | Voice notes arrive from phones, browsers and recorders, each its own Artifact |
| [0104](0104-transcription-and-speakers.md) | Transcripts come from local ASR runs, and speaker spans name consenting people |
| [0105](0105-notebook.md) | Notes are snapshotted as documents, and their links to the archive are claims |
| [0106](0106-set-lists-and-clocks.md) | A set list is a claim of intent, and a clock asserts its own meter |
| [0107](0107-multi-device-and-live.md) | Many devices capture one moment, MIDI is a source, and live responses wait on the node |
| [0110](0110-shared-clock.md) | The node is tempo master, and its clock asserts the meter (proposed) |
