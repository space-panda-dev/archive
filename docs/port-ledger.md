# V2 port ledger

This is the temporary accounting ledger required by
[ADR 0015](decisions/0015-porting-from-v2.md). It covers the V2 spike at commit
`137af2ec8f76c5a039147de204ec040970a05690`. V2 is evidence about mechanisms,
not semantic authority. This ledger is deleted when the port is complete.

Each named unit has exactly one disposition:

- **keep the mechanism**: retain the implementation idea without importing V2
  meaning;
- **translate**: retain the behavior under Archive vocabulary and contracts;
- **rewrite**: replace the implementation because Archive's contract differs;
- **delete**: carry nothing forward;
- **defer**: an open question blocks the behavior. A deferred unit remains
  unsupported and fails loudly.

`Planned` means the disposition is decided here but no port code has landed.
`In progress` means part of the named unit has landed but its cited contract is
not yet complete. `Ported` means the row's final disposition has landed. Later
phases change only the status, unless an accepted decision changes the
authority cited by the row.

Authority shorthand in every reason is normative: `S-n` and `P-n` cite
[the specification](../spec/archive-spec.md), `INV-n` cites
[the invariants](invariants.md), `ADR nnnn` cites the matching
[decision record](decisions/README.md), and an “open” subject cites its named
boundary in [the open questions](open-questions.md). ADR 0015 governs every row
even where a more specific citation is shown.

## Source coverage index

This index makes the file-to-ledger chain explicit. The forbidden source name
is deliberately omitted from the one Cabal manifest and init-shim labels.

| Source files | Ledger coverage |
|---|---|
| `README.md` | Its findings are distributed to the mechanism rows below; Archive's README and ADR 0015 are authoritative. |
| `SPEC.md` | Archive's specification replaces the V2 contract; candidate mechanisms are inventoried below (ADR 0015). |
| `.gitignore` | Its ignored `derived/` and `views/` paths are covered by the open-layout rows. |
| Sole V2 Cabal manifest | “Local orchestration, policy, and packaging inputs” covers names, executables, dependencies, and licence. |
| `door/Door.hs` | “Door core” covers every coherent section. |
| `door/test-canonicalize.sh`, `door/test-check.sh`, `door/test-door-large.sh`, `door/test-door.sh` | The four Door test sections cover every scenario. |
| `tools/ArchiveTypes.hs` | “Reading modules and contracts” covers every function group. |
| `tools/PortableContracts.hs` | Its public `validateDraft`, `validateStored`, `selectCurrent`, `contractManifest`, and `contractCatalog`, plus helper/contract groups, are covered below. |
| `tools/Rec.hs` | “Door and read commands” and “Reading modules” cover every command and whole-store-check group. |
| `tools/Project.hs` | Projection mechanics and open command/layout boundaries are covered below. |
| `tools/UnixTools.hs` | “Local orchestration” covers every command/helper section. |
| `enrich.mk` | Every configuration, discovery, recipe, proposal, and stamp section is covered below. |
| `policy.rules` | Both rules and the annex-metadata mechanism are covered below. |
| Root `test.sh` | Every `check` label is assigned explicitly in “Root shell check labels.” |
| Eight `bin/` shims: apply, V2 init, classify, ingest, policy, project, record, probe | The source-shim row accounts for all eight. |
| `migration/Canonical.hs` | “Migration mechanism” covers the persistent canonicalizer. |
| `migration/adoption.py` | Every helper family, draft family, accounting path, and CLI is covered below. |
| `migration/census.py`, `contracts.py`, `disposition.py` | Every function group and CLI is covered below. |
| `migration/gates.py` | Plumbing, current/view/retention deferrals, backup, aggregation, and CLI are each covered below. |
| `migration/placement.py` | Path, key derivation, plan, apply, serialization, and CLI are each covered below. |
| `migration/continuity.py` | Detector and deferred durable-output halves are separately covered below. |
| `migration/scale.py`, `selection.py` | Isolation/reporting, Archive rewrites, and current-dependent deferrals are covered below. |
| `migration/translate.py` | Every class, helper family, batch path, report, and CLI is covered by the delete row. |
| `migration/run.sh` | Every safety/build/measurement step is listed under “Migration script steps.” |
| Six `migration/fixtures/*.json` files | Each fixture has an individual row below. |
| Fourteen `migration/test_*.py` files | All 238 named methods appear in “Migration unit tests.” |
| Root Nix flake and the V2 package block in `nix/default.nix` | Every pin, derivation, install, exposure, and check piece is covered under packaging. |

| Source document unit | Disposition | Status | Reason |
|---|---|---|---|
| V2 README as a document | delete | Planned | Archive's README and ADR 0015 replace its architecture narrative; measured mechanisms are inventoried separately. |
| V2 specification as a contract | delete | Planned | Archive's specification is semantic authority and every retained mechanism is translated against it (ADR 0015). |
| V2 generated-ignore file | defer | Planned | Its `derived/` and `views/` entries depend on the named on-disk-layout open question. |

## Constitutional findings

These are boundaries for later phases, not decisions made by this ledger.

- V2's `decision` shape becomes Archive's `assessment` shape where it records
  judgment; governance acts become transition claims. The V2
  `action_permission` kind is deleted ([ADR 0003](decisions/0003-record-model.md),
  [ADR 0005](decisions/0005-claims-review-and-authority.md),
  [ADR 0015](decisions/0015-porting-from-v2.md), S-8).
- V2 omits `agent`, hashes non-JCS JSON without `spec`, repeats provenance in
  payloads, and stores location beside artifact meaning. Those all require a
  rewrite (S-9, S-11, P-3; ADR 0003 and ADR 0015).
- V2's hard-coded per-kind `current` selectors are deferred. Archive's rule is
  contract-owned, and the rules remain open (S-18; [open questions](open-questions.md#claims-and-provenance)).
- Archive's exact paths, identifier construction, staging files, admission
  protocol, destroy record, and context versioning are now settled by ADRs
  0020–0028. Ported code must follow those decisions rather than the
  predecessor's forms.
- V2 planning changes git-annex metadata. Archive planning changes nothing;
  apply alone writes changed placement labels (ADR 0008, INV-5, INV-19).
- V2 artifact media boundaries use floating-point seconds. Archive requires
  exact boundaries such as integer sample offsets with a sample rate (S-15).
- V2's package licence is not portable. Archive code is AGPL-3.0-or-later and
  contracts and documentation are CC0-1.0 (ADR 0017).
- [ADR 0019](decisions/0019-publication-approval-is-a-transition.md)
  resolves ADR 0010's publication-approval conflict with ADR 0003, S-8, and
  ADR 0015: approval is a transition claim. Its record shape is still planned.
- The required real-record refusal evidence is now preserved as
  [minimized fixtures](../door/test/fixtures/README.md). Turning those
  observations into Door admission tests is now governed by the accepted core
  artifact payload and staging contracts (ADRs 0023 and 0027).

## Door core

| V2 unit | Disposition | Status | Reason |
|---|---|---|---|
| Envelope and core types (`Id`, ruling/body, reference, draft, stored index, modes/options) | rewrite | In progress | Use four Archive shapes, required `by` and `agent`, opaque object identity, and transition claims (S-8, S-11, P-2, ADR 0015). |
| Four record shapes: artifact | rewrite | Ported | The closed core payload carries only verified content digest and size; location and annex keys stay outside record identity (D-8, INV-6). |
| Four record shapes: entity | translate | Ported | Stable subjects and mutable names fit ADR 0003 and ADR 0012. |
| Four record shapes: claim | translate | In progress | Keep typed versioned claims and exact references under archive-resident contracts (S-8, S-12). |
| Four record shapes: V2 decision | rewrite | Ported | Judgment becomes `assessment`; governance becomes a transition claim (ADR 0003, ADR 0005, ADR 0015). |
| Empty/index insertion and stored-head lookup | translate | Ported | Admission and transition planning index exact committed heads and batch revisions without selecting a domain current (S-10, S-18). |
| Shape names and filename prefixes | translate | Ported | Keep the four shape names while the centralized layout module constructs every digest-addressed record path (D-1, ADR 0015). |
| Legacy-spec allow-list and legacy parser | defer | Planned | Archive-to-Archive specification migration is open. |
| Envelope parser | rewrite | Ported | Distinct closed draft and stored parsers enforce P-2, portable identities, contracts, and Door-owned recording time (D-3, D-7, D-8). |
| Reference parser | rewrite | Ported | Use `{role,id,revision_id?}`; V2 legacy resolution markers need an accepted migration rule (P-4, INV-24). |
| Source provenance parser | translate | Planned | Preserve source provenance in envelope/references, not payloads (S-11, ADR 0015). |
| Contract citation parser | translate | In progress | Preserve identifier/version/digest while loading definitions from the archive (S-12, P-11). |
| Generic payload validation | rewrite | Ported | Core artifact/entity payloads are closed and claim/assessment instances are evaluated fail-closed against installed JSON Schemas (D-6, D-8). |
| V2 decision-value validator | delete | Planned | It validates a rejected shape and dropped permission kind (ADR 0015). |
| Positive-version, non-empty, lowercase-hex checks | keep the mechanism | In progress | Closed, fail-fast primitives support S-8, S-12, and P-11. |
| Path-token rejection | translate | Ported | Identity namespaces and names use the accepted portable grammars before UUIDv5 allocation (D-2, D-7). |
| Stored-record decoding | rewrite | Ported | Stored records require the full closed envelope, canonical revision identifier, and RFC 3339 UTC recording time (D-3, INV-24). |
| Legacy object/reference maps | defer | Planned | They depend on the open specification-migration contract. |
| Reference resolution and exact pins | translate | Ported | Dry-run and persistent batches refuse dangling and wrong-object pins against one committed snapshot (P-4, S-10, INV-24). |
| V2 kind-to-target tables | rewrite | Planned | Target rules belong in archive-resident contracts; current rules stay deferred (S-12, S-18). |
| Create-vs-supersede validation | keep the mechanism | Ported | Dry-run validation permits identical retries and otherwise requires an exact prior revision of the same object and shape (S-8, S-10). |
| SHA-256 helper | keep the mechanism | Ported | SHA-256 is required by P-3 and P-6. |
| Atomic file write | keep the mechanism | Ported | Exclusive immutable record creation and rollback before ref advancement support S-4 and INV-5. |
| Pending/operation path constructors | defer | Planned | Every path must route through a layout module after the layout decision (ADR 0015). |
| Failed-batch recovery and cleanup | translate | Ported | Writers remove installed records and their temporary index after a pre-commit Git failure, and a failed content batch reports the keys it transferred as unadmitted storage without deleting them (S-4, D-4). |
| Git committed-path check | translate | Ported | Immutable record paths are constructed centrally, committed through an isolated index, and later replayed from their unique introducing commits (P-1, D-1). |
| Pending manifest format | rewrite | Ported | The accepted closed proposal manifest names ordered records, identities, contents, and expected heads (ADR 0023). |
| Idempotent remove-if-missing | keep the mechanism | Ported | Operational cleanup tolerates an already-absent path while reporting real removal failures (S-4). |
| Streamed Git pathspec stage/unstage/commit | translate | Ported | A temporary index builds exactly the validated record batch and advances the branch by expected-parent compare-and-swap without staging unrelated paths (P-1, INV-2). |
| Refuse an already-dirty records index | keep the mechanism | Ported | Persistent transition refuses staged changes before constructing its isolated batch commit (S-2, INV-2). |
| Durable operation-ID files | defer | Planned | Idempotency is required, but the durable protocol/location is open (S-4; admission/staging question). |
| CLI option parsing | translate | Ported | Door accepts proposal-directory admission and transition commands with an optional dry-run and refuses stdin mutation (ADR 0023, ADR 0028). |
| Main writer dispatch | rewrite | In progress | Door dispatches both modes through committed-snapshot validation and persistent commit seams, including content-bearing admission; persistent success still predates ADR 0031's power-loss contract (S-2, S-8, S-11). |
| Pure canonicalization/dry run | translate | Ported | Preserve prediction without mutation, using P-3 JCS bytes. |
| JSON object/array/JSONL batch splitting | rewrite | Ported | The closed manifest's `records` array is the sole ordered batch boundary; stdin and JSONL admission are deleted (ADR 0023). |
| Success/failure JSON lines | rewrite | Ported | Dry-run and persistent commands emit one closed result only after the whole proposal succeeds and emit no stdout on refusal (D-4, D-9, INV-24). |
| Operation digest and revision input | rewrite | Ported | RFC 8785/JCS removes `revision_id` and `at` and retains `spec` (P-3). |
| Legacy batch admission | defer | Planned | Old-spec admission depends on migration and staging decisions. |
| Archive batch admission | rewrite | Ported | Admission validates one exact batch, obtains declared content from its configured staging remote, verifies SHA-256 and size through git-annex `calckey` rather than `fsck`, and commits records and content locations once (ADR 0023). |
| Missing-array canonicalization | delete | Ported | P-2 says `refs` and `supersedes` are envelope fields, so a missing array is refused rather than silently invented. |
| Canonical record value and Door timestamp | rewrite | Ported | Identity hashing keeps `spec`, removes `revision_id` and `at`, then Door stamps and retains `at` in canonical stored bytes (P-2, P-3, P-5). |
| Expected-head compare-and-swap | translate | Ported | Both persistent writers recheck exact expected heads and advance the branch only from the validated snapshot after detecting a complete same-commit retry (ADR 0023, ADR 0028). |
| Atomic finish, deduplication, and quiet retry | translate | Ported | Metadata admission and transition write one batch commit; exact retries preserve original bytes and timestamp (P-1, P-5, S-4). |
| Original-byte/git-annex verification | translate | Planned | ADR 0023 requires local presence, fixity, digest, and size verification; implementation waits for the phase-5 INV-5 experiment. |
| Single-host lock | keep the mechanism | Ported | Persistent mutation holds one archive-local advisory lock across validation, recheck, and branch advancement (S-2, ADR 0002). |

## Door and read commands

| Command | Disposition | Status | Reason |
|---|---|---|---|
| V2 writer version output | translate | Planned | Retain version reporting under Archive command names. |
| V2 JSONL writer | translate | Planned | Becomes `door admit`; input/staging details remain provisional (ADR 0015). |
| Writer commit message option | translate | Planned | Git commit messages are P-1 mechanism, not archival semantics. |
| Writer operation ID | translate | Planned | Preserve retry identity subject to the accepted admission protocol (S-4). |
| Writer expected-head fencing | translate | Ported | Admission proposals pin every created or revised object and the persistent writer rechecks the snapshot before compare-and-swap (ADR 0023). |
| Duplicate expected-head alias | delete | Planned | A compatibility alias carries no Archive meaning. |
| Writer canonicalize mode | translate | Ported | Becomes Door dry-run using JCS (P-3). |
| `rec add` | translate | Ported | `door admit` commits metadata and content-bearing batches through Door (INV-2, ADR 0023). |
| `rec ls` | translate | Ported | `archive ls` reads and summarizes only records from one pinned committed tree (INV-2, INV-24, section 6). |
| `rec show` | translate | Ported | `archive show` selects an exact object or revision from one pinned committed tree and distinguishes a complete empty result (INV-24, section 6). |
| Generic revision head | translate | Planned | Keep a clearly named revision-head projection; never call it domain current (S-18). |
| Domain current/domain projection | defer | Planned | Contract rules for current are open. |
| Accepted query | translate | Planned | Query assessments without importing a universal review lifecycle (ADR 0005). |
| About query | translate | Planned | Read-only subject lookup is a projection (ADR 0011). |
| Artifact path query | defer | Planned | It depends on layout and external location state. |
| Claim convenience writer | rewrite | Planned | Empty contracts and missing agent/provenance violate S-11 and S-12. |
| Whole-store check | translate | Ported | `archive doctor` checks references, exact pins, lineage, assessments, and cited contracts while retaining incomplete-read evidence (S-10, S-12, INV-24). |
| Contract catalog | rewrite | Ported | Archive 0.0.2 verifies and copies the exact five specification-owned bundles while retaining the immutable version 1 bytes (D-6, D-8, ADR 0030); 0.0.5 verifies and copies all twenty-four, keeping every earlier bundle's bytes (D-13, D-14); 0.0.7 copies thirty (D-15). |
| `archive init` | rewrite | Ported | Initialization creates the accepted layout, identities, local resources, bootstrap records, Git repository, and git-annex repository in one archival commit (D-1, D-2, D-7, D-10). |
| `door admit` | rewrite | In progress | Dry-run and persistent modes enforce exact proposals, heads, one lock, one commit, and quiet retry, including content; persistent success still predates ADR 0031's power-loss contract (D-4, ADR 0023). |
| `door transition` | rewrite | Ported | Dry-run and persistent modes dispatch exact retire/destroy contracts, replay immutable history, and enforce authorization, heads, state, durable v2 order, one lock, one commit, and quiet retry (D-9, ADR 0030, INV-2). The specification upgrade of 0.0.7 is installed but refused as unsupported until its dispatch exists (D-15, ADR 0068). |

## Reading modules and contracts

| V2 unit | Disposition | Status | Reason |
|---|---|---|---|
| Committed-tree archive loader | keep the mechanism | Ported | Readers pin one Git commit and read the manifest, local specification resources, context, contracts, and records only from that tree (INV-2, INV-4, D-1). |
| Tree fingerprint | rewrite | Ported | One resolved commit identifier pins every subprocess; failures and changed evidence are never collapsed to empty (INV-24). |
| Git/tar committed-tree stream | translate | Ported | Digest-addressed record reads retain usable records and surface every per-path or subprocess failure (INV-24). |
| JSONL parsing | delete | Ported | D-1 chose one closed JSON record per digest-addressed file, so a committed JSONL store is not part of Archive's layout. |
| Atomic tree-keyed JSONL cache | keep the mechanism | Planned | The cache is disposable and keyed by evidence (INV-4). |
| Generic JSON/envelope accessors | translate | Ported | Readers retain raw committed values and exact bytes beside the strict Archive envelope (S-8, S-11, INV-24). |
| Reference/supersession accessors | keep the mechanism | Ported | Doctor follows exact pins and explicit lineage without choosing domain current (P-4, S-10). |
| Generic matching helpers | keep the mechanism | Ported | `ls` and `show` project committed evidence by exact object or revision without owning meaning (ADR 0011). |
| Generic superseded-revision filtering | translate | Planned | Retain only as revision-head projection, not per-kind current (S-18). |
| Central artifact-path builder | defer | Planned | Keep the one-module seam; choose no path before the layout decision (ADR 0015). |
| Explicit contract errors | keep the mechanism | Ported | Closed read issues distinguish acquisition, absence, malformed evidence, and unsupported interpretation (INV-24, section 6). |
| Draft/stored contract validation | translate | Ported | Door and readers share strict stored-envelope, digest, canonical-bundle, schema, and context-collision validation (S-12, D-6). |
| Hard-coded current selector | defer | Planned | Per-kind current rules are open and must be contract data (S-18). |
| Canonical contract manifest/catalog bytes | translate | Ported | RFC 8785 bundle bytes determine their SHA-256 paths and citations, and the loader verifies both (D-6, D-8). |
| Artifact contract | rewrite | Planned | Remove location/annex key and add Archive provenance (S-9, S-11). |
| Entity contract | translate | Planned | Keep stable subject/name distinction (ADR 0012). |
| Technical metadata contract | translate | In progress | Derived evidence pins exact inputs and records run configuration (ADR 0006). Specification 0.0.7 installs `derivation_run` and `audio_stream`, and Door admits them with every rule checked (D-15, ADRs 0064 and 0065); World projects runs, stream facts, span suggestions, and version 2 matches (projection 6, revision 3); the probe tool is not ported. |
| Label contract | defer | Planned | Representation and current rule remain open. |
| Transcript contract | defer | Planned | Representation/current rule and remote disclosure adapter remain open. |
| Selection contract | rewrite | Planned | Preserve ordered pins but replace float seconds with exact boundaries (S-15). |
| Annotation contract | defer | Planned | Annotation and assessment representation/versioning remain open. |
| Continuous-recording contract | defer | Planned | Detector mechanism survives; durable contract/current rule is open. |
| Musical-analysis contract | defer | Planned | Claim representation/current rule remains open. |
| Feature-timeline contract | defer | Planned | Claim representation/current rule remains open. |
| Source-metadata contract | defer | Planned | Promotion/claim representation remains open. |
| Publication contract excluding approval's record shape | rewrite | Planned | Enforce settled destination, rights, sensitivity, and prior-disclosure rules (S-29–S-32). |
| Publication approval record shape | rewrite | In progress | Accepted ADR 0019 makes approval a transition claim with pinned revisions and destination or exposure class; 0.0.5 installs it and `door transition` commits approvals, hold-backs, and withdrawals by the active operator (ADR 0051). Translating V2 approvals remains. |
| Assessment contract | rewrite | Planned | Use exact claim pins and Archive assessment vocabulary (ADR 0005). |
| Action-permission contract | delete | Planned | ADR 0015 drops it. |
| Citation/reference validators | keep the mechanism | Planned | Exact pins and digests fail closed (S-12, INV-24). |
| Conflict/current grouping helpers | defer | Planned | They encode V2 per-kind verdicts (S-18). |
| Basic field/time/digest helpers | keep the mechanism | Planned | Closed validation is contract mechanism. |
| Portable-ID check | translate | Planned | Keep refusal while exact object IRI remains open. |
| Whole-store dangling/wrong-pin/cross-object/fork/cycle checks | translate | Ported | `archive doctor` reports each structural violation through the public command (S-10, INV-24). |
| Check result JSON and summary | keep the mechanism | Ported | Closed JSON responses preserve coverage, structural findings, and incomplete-read issues separately (INV-24, section 6). |
| View projection module | defer | Planned | Projection is regenerable, but its command and layout are open (INV-4). |
| Claim grouping and summaries for views | translate | Planned | Projections may group claims without becoming authority (ADR 0011). |
| Annex held-here observation | keep the mechanism | Planned | It reports managed custody without changing meaning (ADR 0009). |

## Local orchestration, policy, and packaging inputs

| V2 unit | Disposition | Status | Reason |
|---|---|---|---|
| Native multicall dispatch | keep the mechanism | Planned | One implementation with stable command aliases avoids duplicate seams. |
| Process, JSON, and hashing helpers | keep the mechanism | Planned | Small fail-closed external boundaries are reusable. |
| V2 ingest command | rewrite | Planned | Becomes admission/staging through Door; current command commits storage before the contract is settled (INV-2). |
| WAV-only discovery | defer | Planned | Media selection is desired state/policy, not a default admission rule. |
| Source-plus-filename object ID | rewrite | Ported | Object IDs are deterministic UUIDv5 URNs under the archive UUID and an accepted namespace/name allocation, never a source path or filename (D-2, S-9). |
| Original copy and draft construction | defer | Planned | Layout, staging, and in-record location are open or rejected. |
| Existing-occurrence idempotence check | translate | Planned | Keep no-op retry after the identifier/admission decisions (INV-5). |
| V2 init command | rewrite | Ported | `archive init` now copies the exact local specification resources and records the operator bootstrap under the accepted layout (D-1, D-7). |
| Installed resource lookup | translate | Planned | Keep package-resource lookup under Archive package names/licences. |
| V2 policy command | rewrite | Planned | Planning must not write annex metadata; policy is authorized evidence and plans are bound (INV-16–INV-19). |
| V2 rule parser and relative dates | defer | Planned | Policy grammar, stable names, freshness, and authorship are open. |
| Raw TSV apply | rewrite | Planned | Plans must cite policy/evidence and revalidate before acting (S-28, INV-19). |
| Safe plan path and strict TSV parser | keep the mechanism | Planned | Retain input hardening below a future bound-plan interface. |
| Enrichment build configuration | translate | Planned | Configuration stays separate and is recorded as run input (ADR 0006, ADR 0007). |
| Object+revision derivation stamp | keep the mechanism | Planned | Disposable stamps bind exact evidence and support quiet history (INV-4, INV-5). |
| Desired enrichment discovery | defer | Planned | The policy/mechanism boundary and grammar are open. |
| Standalone probe/classifier recipes | translate | Planned | Tools take files and propose results through Door (ADR 0006). |
| Unrecorded classifier configuration | rewrite | Planned | Parameters and presets must be recorded as run inputs (ADR 0006). |
| Recipe proposal to Door | translate | Planned | Keep the call chain and assert the real dispatcher reaches Door (INV-2). |
| V2 hot placement rule | rewrite | Planned | Desired placement belongs in authorized policy, not annex expressions (ADR 0007). |
| V2 cold placement rule | rewrite | Planned | Same; no-policy planning yields an empty plan. |
| Facts copied into annex metadata | delete | Planned | ADR 0008 explicitly rejects the mechanism. |
| V2 source-tree shims: apply, init, classifier, ingest, policy, project, record reader, probe | delete | Planned | Package real binaries; duplicate wrappers carry no durable contract. |
| Native Cabal package/no runtime compiler | keep the mechanism | Ported | Cabal builds separate native `door` and `archive` executables with packaged local specification resources (ADR 0014, D-7). |
| Writer executable | translate | Planned | Rename to Door and expose admit/transition. |
| Reader executable | translate | Ported | The `archive` binary exposes `status`, `doctor`, `ls`, and `show`, while `current` fails at its named open boundary (section 6, S-18). |
| Projection executable | defer | Planned | Views command/layout are open. |
| Shared native multicall executable | keep the mechanism | Planned | Reuse packaging mechanism with Archive command aliases. |
| Haskell dependencies and warnings | keep the mechanism | Planned | They support the language boundary in ADR 0014. |
| V2 package licence | rewrite | Planned | Apply AGPL-3.0-or-later/CC0 split (ADR 0017). |
| Enrichment binaries excluded from Door package | keep the mechanism | Planned | Standalone tools preserve the enrichment boundary (ADR 0006). |
| Root flake pin/system fan-out | keep the mechanism | Ported | The locked Archive flake declares Darwin and Linux systems; reproducible packaging is mechanism, not archive meaning. |
| V2 Nix Haskell derivation | translate | Ported | The flake builds Archive's native Door and reader package and runs both Cabal suites (ADR 0014). |
| V2 templates and command aliases in `postInstall` | rewrite | Planned | Install Archive spec/contracts and only accepted commands. |
| V2 package exposure | translate | Ported | The flake exposes the Archive package as `archive` and `default`. |
| Installed-binary/version/template check | rewrite | Planned | Assert Archive commands, spec, contracts, licence, and static closure. |
| Isolated installed-reader/contract checks | translate | Planned | Retain real-package fixtures under Archive contracts. |
| Sibling Spool/enrichment integration checks | translate | Planned | Test only published program/protocol seams (INV-22). |

## Migration mechanism

| V2 file or section | Disposition | Status | Reason |
|---|---|---|---|
| Aeson canonicalizer process | rewrite | Planned | Keep a persistent helper, but implement RFC 8785/JCS (P-3). |
| Adoption fail-closed primitives | translate | In progress | Preserve no-fabrication and explicit reasons (INV-24). |
| Supersession/lineage walkers and cycle detection | translate | In progress | Preserve explicit lineage while removing V2 current verdicts (S-10, S-18). |
| Adoption identity/source/contract/artifact helpers | rewrite | In progress | Emit Archive identities, provenance, contracts, and external locations (S-9, S-11, S-12). |
| Adoption reference/value rewriting | rewrite | Planned | Only declared exact references survive; provenance leaves payloads (S-11, P-4). |
| Adoption draft constructors | rewrite | In progress | Translate review states to assessments and governance/state changes to claims (ADR 0003, ADR 0005, ADR 0015). |
| Adoption accounting/precheck/reference-survival report | keep the mechanism | In progress | Every source record gets one route or explicit unresolved reason (ADR 0015, INV-24). |
| Adoption orchestration | translate | Planned | Keep exhaustive routing with Archive destinations and no guesses. |
| Two-pass Door prediction/admission and drift demotion | rewrite | In progress | Use Archive Door/JCS and refuse any changed draft (INV-2, P-3). |
| Production-schema census and writer/reader scan | translate | Planned | Preserve the severed-chain audit against Archive contracts and real writers. |
| Contract-citation census | translate | In progress | Count distinct top-level V2 contract path stamps per source record; malformed stamps fail closed, while Archive-resident citation reconciliation remains unfinished (ADR 0015, S-12). |
| Explicit 46-schema disposition catalog | translate | In progress | The census uses the exhaustive source-name catalog and accounts each observed schema; routes remain unresolved until Archive authority defines them (ADR 0015, INV-24). |
| Disposition aggregation and malformed-schema refusal | keep the mechanism | In progress | Unknown schemas, malformed JSON, duplicate keys, non-finite numbers, blank lines, and source trees without schema-tagged records fail closed; destination aggregation remains unsupported (INV-24). |
| Gate command/report plumbing | keep the mechanism | Planned | Independent failures remain data, not swallowed exceptions (INV-24). |
| V2-vs-production current comparison | defer | Planned | It depends on open per-kind current rules. |
| Generated-view comparison | defer | Planned | View layout and current projection are open. |
| Retention-plan comparison | defer | Planned | Policy grammar, freshness, and failure domains are open. |
| Backup/isolated-restore comparison | translate | Planned | Preserve whole-store validation and evidence-count equality (S-5). |
| Gate aggregation/CLI | translate | Planned | Keep every gate reporting even when another fails (INV-24). |
| Central original path in placement migration | defer | Planned | Layout is open. |
| git-annex extension/key derivation | keep the mechanism | Planned | Retain measured SHA256E/reinject mechanics (ADR 0009, ADR 0015). |
| Placement planning | translate | Planned | Planning is read-only and bound to evidence/policy (S-28). |
| Placement apply/admission | rewrite | Planned | Write changed labels before annex acts, reject stale plans, and use reinject only (ADR 0008, ADR 0015). |
| Placement plan JSONL/CLI | translate | Planned | Keep serialization after Archive plan schema exists. |
| Recorder filename/rollover grouping | keep the mechanism | Planned | Deterministic enrichment below policy may run locally; it asserts nothing by itself (ADR 0006). |
| Continuous-recording ID/draft/report admission | defer | Planned | Contract and current semantics are open. |
| Scale harness isolation and private/public report split | keep the mechanism | Planned | Synthetic bounded measurements remain regenerable and contain no personal data (INV-4, INV-23). |
| Scale harness Door/read/current calls | rewrite | Planned | Use Archive Door and do not call unresolved current selectors. |
| Scale current comparison | defer | Planned | Current rules are open. |
| Scale orchestration | translate | Planned | Preserve bounded measurement under Archive commands. |
| Selection-count probe | defer | Planned | It compares unsettled current projections. |
| Original lossy translation probe, every function and CLI | delete | Planned | The later adoption path supersedes it; retaining two translators risks severed dispatch and lost records (ADR 0015). |

## Migration script steps and fixtures

| Unit | Disposition | Status | Reason |
|---|---|---|---|
| Run-script argument/input/output safety | keep the mechanism | In progress | A read-only source-tree manifest checks regular files, digests, and detected scan changes on synthetic fixtures; immutable-copy creation, output isolation, and rollback remain unfinished (INV-23, ADR 0015). |
| Build V2 canonicalizer | rewrite | Planned | Build Archive JCS canonicalizer (P-3). |
| Build V2 Door | rewrite | Planned | Build Archive Door (INV-2). |
| Run census | translate | In progress | The raw JSON/JSONL census accounts for source values and failures and can bind those exact file paths, bytes, and SHA-256 digests to a complete source-manifest report; the caller still must supply a frozen source, and writer/reader scans remain unfinished (ADR 0015, INV-24). |
| Run disposition catalog | translate | In progress | The census reports every catalog schema as unresolved and conserves observed schema-tagged counts; migration routing remains unsupported (ADR 0015, INV-24). |
| Run contract census | translate | Planned | Validate contract availability. |
| Run selection comparison | defer | Planned | Current is open. |
| Run obsolete translator | delete | Planned | Use only the adoption translator. |
| Run scale measurement | translate | Planned | Keep isolated bounded measurement. |
| Final measurements report | keep the mechanism | Planned | It is non-authoritative output outside the archive. |
| Cross-artifact selection fixture | rewrite | Planned | Rebuild from a real writer or minimal stripped real record; use Archive exact pins and boundaries (S-12, S-15, INV-23). |
| Vertical migration fixture | rewrite | Planned | Preserve dependency coverage but use minimal stripped real-writer records and Archive routes (ADR 0015, INV-23). |
| Continuity fixture | defer | Planned | Durable contract/current semantics are open. |
| Performance/supersession fixture | rewrite | Planned | Use Archive claims/assessments and exact provenance (ADR 0003, ADR 0005). |
| Selection-chain fixture | defer | Planned | It depends on current rules. |
| Supersession-chain fixture | rewrite | Planned | Preserve exact lineage using Archive envelopes (S-10). |

## Test dispositions

Every V2 test below is named. Semicolon-separated names in one row share that
row's disposition and reason.

### Door canonicalization tests

| Tests | Disposition | Status | Reason |
|---|---|---|---|
| JSONL ordered results; array-input invariance; pretty-object invariance | translate | Planned | Preserve deterministic indexed results under JCS (P-3, INV-24). |
| prediction equals admission; re-admitting predicted records; tampered revision refused | rewrite | Planned | Assert the Archive envelope and P-3 hash, including `spec` and excluding `at`. |
| mixed valid/invalid batch; unparseable input | rewrite | Planned | Use Archive shapes/contracts and retain per-item explicit failure (S-8, INV-24). |
| no mutation in a Git repository; repeated output byte-identical | keep the mechanism | Planned | Dry-run changes nothing and is deterministic (S-4, INV-5). |
| no mutation in an arbitrary non-archive directory | delete | Planned | Behavior outside an initialized archive is not a contract. |

### Door whole-store tests

| Tests | Disposition | Status | Reason |
|---|---|---|---|
| good-store counts/no errors | rewrite | Planned | Rebuild with Archive shapes, agent provenance, and archive-resident contracts. |
| dangling reference; wrong-object pin; cross-object supersession; edited contract digest | translate | Planned | These become permanent `archive doctor` refusals (S-10, S-12, INV-24). |
| legacy unresolved reference | defer | Planned | Migration representation is open. |
| unknown doctor flag | translate | Planned | Keep explicit usage failure under `archive doctor`. |
| 20,000-record streamed batch | translate | Planned | Keep pathspec-scale mechanism under the accepted staging protocol. |

### Door admission and contract tests

| Tests | Disposition | Status | Reason |
|---|---|---|---|
| version output | translate | Planned | Rename to Archive Door. |
| exact V2 contract count/digests | rewrite | Planned | Assert the archive-resident Archive catalog, not a V2 count (S-12). |
| V2 action-permission catalog and records | delete | Planned | The kind is dropped (ADR 0015). |
| legacy retry | defer | Planned | Specification migration is open. |
| operation-ID payload conflict | translate | Planned | Preserve idempotent conflict detection (S-4). |
| canonical retry; assessment exact pin; same-object replacement; invalid supersedes role | rewrite | Planned | Use Archive envelope/JCS/assessment vocabulary (S-8, P-3, P-4). |
| stale expected head | defer | Planned | Admission fencing is not yet accepted. |
| dangling and wrong-object references | translate | Planned | Exact pins fail closed (P-4, INV-24). |
| managed/unmanaged artifact admissions and URI/key combinations | rewrite | Planned | Location and annex key leave the record (S-9, INV-6). |
| capture time validation | translate | Planned | Preserve exact/unknown capture time; Door stamps only `at` (P-5). |
| derived artifact pin, missing pin, wrong target | translate | Planned | Exact derivation provenance is settled (ADR 0006). |
| technical metadata fields and citation | defer | Planned | The kind contract is open. |
| ordered selection; swapped-order identity; span/negative/string bounds; refs/citations | rewrite | Planned | Use exact sample boundaries and Archive contracts (S-12, S-15). |
| selection empty/extra-field cardinality | defer | Planned | Contract details are open. |
| payload-only extra provenance survives | rewrite | Planned | Provenance belongs in envelope/references (S-11). |
| musical analysis; transcript versions/targets/current; annotation fields/targets/current; labels/current; source metadata; feature timeline | defer | Planned | Per-kind contracts and current rules are open. |
| publication admission/target excluding approval shape | rewrite | Planned | Test settled rights, sensitivity, destination, and prior-disclosure rules (S-29–S-32). |
| publication approval shape | rewrite | In progress | Accepted ADR 0019 requires a transition claim with pinned revisions and destination or exposure class; Door's transition tests cover approval, hold-back, withdrawal, and each refusal (ADR 0051). |
| continuous-recording float-offset contract tests | delete | Planned | The V2 contract violates S-15; detector mechanism is tested separately. |
| continuous-recording current/conflict tests | defer | Planned | Current rules are open. |
| cited assessment and bad digest | rewrite | Planned | Use Archive assessment shape and exact contract citation. |
| generic artifact/selection/transcript domain-current chains | defer | Planned | Current rules are open. |
| failed pre-commit cleanup; interrupted orphan recovery | translate | Planned | Preserve atomic no-state-on-failure behavior (S-2, S-4). |

### Root shell check labels

These are the exact labels passed to the V2 shell test's `check` function.

| Test label | Disposition | Status | Reason |
|---|---|---|---|
| one artifact per file | rewrite | Planned | Admission and object identity change under ADR 0015 and P-9. |
| ingesting a card again adds nothing | keep the mechanism | Planned | Preserve quiet idempotency (INV-5). |
| capture date comes from the file | translate | Planned | Keep capture time distinct from Door's `at` (P-5). |
| the ingested object id matches ingest's own derivation | rewrite | Planned | Source-plus-name can collide; object identity is opaque (S-9, P-9). |
| rec path prints the object-id-keyed layout (D1) | defer | Planned | The exact on-disk layout is a named open question. |
| index ignores a record before its commit | keep the mechanism | Planned | Readers expose committed evidence only (INV-2). |
| show ignores a record before its commit | keep the mechanism | Planned | Readers expose committed evidence only (INV-2). |
| index exposes the record after its commit | keep the mechanism | Planned | Readers expose committed evidence only (INV-2). |
| a misspelt optional field is refused, not ignored | translate | Planned | Archive validation fails closed (INV-24). |
| a dangling reference is refused | translate | Planned | Unresolved references are explicit failures (INV-24). |
| one bad line refuses the whole batch | keep the mechanism | Planned | Door admission is atomic (INV-2, INV-1). |
| an artifact whose file is not in the archive is refused | defer | Planned | Verification depends on the named admission/staging open question. |
| an artifact naming another file's key is refused | rewrite | Planned | Keep content verification but move annex location out of the record (S-9). |
| a record added twice is written once | keep the mechanism | Planned | Preserve quiet retry (INV-5). |
| two claims per recording | defer | Planned | Desired enrichment and claim contracts remain open. |
| the long one is a performance | defer | Planned | Classification vocabulary and policy remain open. |
| a second run has nothing to be done | keep the mechanism | Planned | Preserve quiet regenerable work (INV-4, INV-5). |
| losing make's stamps writes nothing new | keep the mechanism | Planned | Regeneration must not duplicate evidence (INV-4, INV-5). |
| a claim nobody ruled on is not accepted | translate | Planned | Query Archive assessments, not V2 decisions (ADR 0005). |
| an accepted claim is accepted | translate | Planned | Preserve exact scoped judgment as an assessment (ADR 0005). |
| a decision superseded by a rejection no longer accepts | translate | Planned | Translate judgment history to assessments; current rules stay contract-owned (ADR 0005, S-18). |
| every recording is linked by source, date and class | defer | Planned | View layout and class currentness are open. |
| a view resolves to the original's bytes | translate | Planned | Keep a regenerable projection that never counts as a copy (INV-4, INV-13). |
| each recording has a readable summary | translate | Planned | Keep non-authoritative projection output (ADR 0011). |
| project groups claims in Haskell | delete | Planned | Test observable cost, not internal implementation structure (ADR 0014). |
| policy groups claims in Haskell | rewrite | Planned | Planner is Python/clingo and policy grammar remains open (ADR 0014). |
| the laptop drops only the old fragment; the drive gets everything | rewrite | Planned | Use a bound plan and failure domains (INV-13, INV-19). |
| a key two recordings share carries both their capture dates | delete | Planned | Planning must not copy occurrence facts into annex metadata (ADR 0008). |
| a drop before any other copy exists is refused, and the file stays | keep the mechanism | Planned | Keep git-annex safety below placement policy (ADR 0009). |
| the full plan applies | rewrite | Planned | Apply revalidates a bound plan before acting (INV-19). |
| the laptop no longer holds the old fragment | rewrite | Planned | Assert the accepted plan/apply and failure-domain semantics (INV-13, INV-19). |
| the drive holds everything | rewrite | Planned | Assert the accepted plan/apply and failure-domain semantics (INV-13, INV-19). |
| nothing is left to do | rewrite | Planned | Preserve no-op history under the new planner (INV-5, INV-19). |
| only the new version's class claims are current | defer | Planned | Per-kind current is a named open question. |
| the old claims are still in the archive | keep the mechanism | Planned | Supersession and judgment never erase evidence (INV-7). |
| an unchanged claim from the new version is not rewritten | keep the mechanism | Planned | Preserve quiet history (INV-5). |
| the views follow the new claims | defer | Planned | View layout and current semantics are open. |
| a recording held only on the drive is still in the views, and says so | translate | Planned | Report custody without making the projection authoritative (INV-4, INV-13). |
| the spool result's output equals classify's own answer | translate | Planned | Test Archive's future adapter while Spool stays independent (INV-22). |
| a spool worker's result streams a claim into the door | translate | Planned | A result gains standing only through Door (INV-2, INV-22). |
| execution provenance carries tool, inputs, parameters and contracts | translate | Planned | Preserve derivation provenance in envelope/references (ADR 0006). |
| output.class is in the stored claim | translate | Planned | Keep contract-defined result content (S-12). |
| completed execution leaves no lease | delete | Planned | Lease state belongs to Spool, not Archive (INV-22). |
| retrying the same task is idempotent | translate | Planned | Exercise the published Spool seam without porting its state (INV-22). |
| retrying admission returns the original durable claim | keep the mechanism | Planned | Door retry is quiet (INV-5). |
| a crashing task's failure names the exit code and is retried | translate | Planned | Exercise explicit failure at the Spool adapter seam (INV-22, INV-24). |
| a retried crash task is back in pending | translate | Planned | Pending is external Spool state (INV-22). |
| an unconfigured capability fails without retry | translate | Planned | The Archive adapter fails closed (INV-22, INV-24). |
| an unconfigured capability's task is not requeued to pending | translate | Planned | The Archive adapter fails closed (INV-22, INV-24). |
| an abandoned lease is reclaimed into pending | translate | Planned | Keep Spool reclaim distinct from an Archive reclaim verdict (INV-22). |
| deleting the index rebuilds current reads | translate | Planned | Preserve disposable indexes; replace the unresolved current query (INV-4, S-18). |
| an execution claim is not accepted without a decision | rewrite | Planned | Replace decision with an exact assessment (ADR 0005). |
| a current accept decision makes the claim accepted | rewrite | Planned | Replace decision with an exact assessment; current rule remains contract-owned (ADR 0005, S-18). |
| an action permission never changes claim acceptance | delete | Planned | The V2 kind is dropped (ADR 0015). |
| audio label citation is derived from canonical bytes | translate | Planned | Assert Archive contract bytes/digests (S-12). |
| audio transcript citation is derived from canonical bytes | translate | Planned | Assert Archive contract bytes/digests (S-12). |
| suggested audio label is not domain-current | defer | Planned | Per-kind current is open. |
| reviewed audio label is domain-current | defer | Planned | Per-kind current is open. |
| two reviewed labels about different artifacts do not collide | defer | Planned | Per-kind current/key semantics are open. |
| reviewed label is domain-current before being reverted | defer | Planned | Per-kind current is open. |
| a non-qualifying successor still retires its predecessor (audio_label) | defer | Planned | V2's successor verdict cannot answer the current-rule question. |
| label-revert-slot has no current label once its successor reverted to suggested | defer | Planned | Per-kind current is open. |
| planned transcript is not domain-current | defer | Planned | Per-kind current is open. |
| operator-reviewed transcript supersedes machine transcript | defer | Planned | Per-kind current is open. |
| a rejected transcript retires its predecessor rather than reviving it | defer | Planned | Rejection/current interaction is open. |
| a rejected transcript leaves its selection with no current transcript from that chain | defer | Planned | Rejection/current interaction is open. |
| audio selection citation is derived from canonical bytes | translate | Planned | Assert Archive contract bytes/digests (S-12). |
| rec contracts exposes the audio_selection citation id | translate | Planned | Read archive-resident contract identity (S-12). |
| rec contracts exposes the audio_selection citation version | translate | Planned | Read archive-resident contract identity (S-12). |
| rec contracts exposes all 13 portable kinds | rewrite | Planned | Assert the accepted Archive catalog, not V2's count (S-12). |
| an admitted audio selection is domain-current (selector=none) | defer | Planned | Per-kind current is open. |
| audio annotation citation is derived from canonical bytes | translate | Planned | Assert Archive contract bytes/digests (S-12). |
| a reviewed annotation is domain-current | defer | Planned | Annotation representation and current are open. |
| domain-current returns only the edited annotation's head | defer | Planned | Per-kind current is open. |
| domain-current shows the edited revision, not the original | defer | Planned | Per-kind current is open. |
| two annotations about different selections do not collide | defer | Planned | Annotation identity/current semantics are open. |
| continuous recording citation is derived from canonical bytes | defer | Planned | The durable continuity contract is open. |
| a continuous recording spans two ingested artifacts and is domain-current | defer | Planned | The durable contract/current rule is open. |
| a selection with a piece on each recording part is admitted about the same two pins | rewrite | Planned | Keep exact pins but use Archive exact boundaries/contracts (P-4, S-15). |
| domain-current still shows the recording once alongside its selection | defer | Planned | Per-kind current is open. |
| a second recording starting at a different artifact does not collide | defer | Planned | Continuity identity/current semantics are open. |
| unknown contract version is atomic | translate | Planned | Contract failure is atomic and explicit (S-12, INV-24). |
| missing contract citation is atomic | translate | Planned | Contract failure is atomic and explicit (S-12, INV-24). |
| bad contract digest is atomic | translate | Planned | Contract failure is atomic and explicit (S-12, INV-24). |
| duplicate contract citation is atomic | translate | Planned | Contract failure is atomic and explicit (S-12, INV-24). |
| legacy-unpinned rich subject is atomic | defer | Planned | Legacy unresolved representation depends on migration rules. |
| project succeeds on two same-source same-name artifacts with distinct object ids | rewrite | Planned | Anchor distinct identities before testing an accepted layout (S-9). |
| project links both colliding artifacts by source, not one over the other | defer | Planned | View layout is open. |
| project links both colliding artifacts by date, not one over the other | defer | Planned | View layout is open. |
| collision artifact a is linked at its own object-id path | defer | Planned | View layout is open. |
| collision artifact b is linked at its own object-id path | defer | Planned | View layout is open. |
| project succeeds on an artifact with no captured_at | translate | Planned | Unknown capture time remains explicit (P-5, INV-24). |
| artifact with no captured_at is linked by source | defer | Planned | View layout is open. |
| artifact with no captured_at is linked under by-date/unknown | defer | Planned | View layout is open. |

### Migration unit tests

The following named groups are exhaustive for every `test_*.py` method in the
V2 migration directory.

| Test file and named methods | Disposition | Status | Reason |
|---|---|---|---|
| `test_census.py`: `test_census_uses_the_exact_source_catalog_and_keeps_all_routes_unresolved`; `test_source_catalog_matches_the_v2_vocabulary_fingerprint`; `test_cli_uses_catalog_and_fails_for_unresolved_or_unknown_schemas`; `test_real_cli_dispatch_counts_distinct_top_level_source_contract_stamps`; `test_complete_source_manifest_binds_exact_paths_bytes_and_hashes`; `test_source_manifest_binding_detects_changed_bytes`; `test_source_manifest_binding_detects_omitted_and_extra_files`; `test_invalid_source_manifest_fails_closed`; `test_real_cli_dispatch_accepts_source_manifest_binding`; `test_source_manifest_fifo_is_refused_without_blocking`; `test_source_manifest_binding_detects_file_added_after_initial_rescan`; `test_contract_citation_census_refuses_malformed_source_stamps`; `test_json_jsonl_timestamps_unknown_missing_schema_and_non_record_are_accounted`; `test_unsupported_file_and_missing_schema_are_explicit`; `test_unknown_and_missing_schema_are_incomplete_without_parse_failures`; `test_top_level_field_shapes_count_optional_fields_without_leaking_values_or_ids`; `test_zero_entry_scans_are_not_success_shaped`; `test_valid_non_record_json_is_incomplete_without_source_records`; `test_mixed_source_records_and_non_record_units_are_incomplete`; `test_source_contract_stamp_coverage_is_incomplete_when_a_file_cannot_be_read`; `test_duplicate_object_keys_are_malformed_at_top_level_and_nested_in_json_and_jsonl`; `test_non_finite_values_and_blank_jsonl_lines_fail_closed`; `test_unreadable_file_is_not_reported_as_an_empty_census` | translate | In progress | Keep census dispatch anchored to the exact 46-name inventory and conserve declared and unknown schema counts; optional manifest binding remains incomplete on mismatch and reports that it is not an atomic snapshot (ADR 0015, INV-24). |
| `test_contracts.py`: `test_counts_only_distinct_top_level_contract_citations` | translate | In progress | Count distinct top-level V2 source contract path stamps per record without reading payload text; Archive-resident reconciliation remains unfinished (ADR 0015, S-12). |
| `test_disposition.py`: `test_catalog_is_exactly_the_46_schema_vocabulary`; `test_synthetic_census_counts_conserve_into_destinations`; `test_catalog_keeps_outer_shapes_separate_from_absorbed_state`; `test_song_performance_becomes_an_audio_annotation_claim`; `test_record_revision_keeps_envelope_destination_with_claim_outer_shape`; `test_unknown_schema_is_reported_without_being_forced_into_a_shape`; `test_cli_writes_aggregate_json`; `test_malformed_schema_entry_cannot_report_complete_accounting` | translate | Planned | Preserve exhaustive, conservative accounting while translating destinations (ADR 0015, INV-24). |
| `test_disposition.py`: `test_session_is_unresolved_not_guessed` | defer | Planned | Session/continuity contract is open. |
| `test_cross_artifact.py`: `test_two_piece_selection_is_exact_ordered_admissible_and_consumable`; `test_changing_either_artifact_changes_its_pin_and_stales_the_selection`; `test_unpinned_piece_is_refused_by_the_door` | rewrite | Planned | Use Archive Door, exact pins, JCS, and exact boundaries (P-3, P-4, S-15). |
| `test_adoption.py`: `test_all_46_schemas_are_accounted_for_without_opaque_operational_claims`; `test_the_three_batch_records_all_route_to_git`; `test_every_emitted_record_is_a_v2_draft`; `test_only_declared_reference_paths_are_rewritten`; `test_missing_artifact_facts_and_subjects_are_unresolved`; `test_contract_manifest_is_injected_without_local_digest_constants`; `test_artifact_join_failure_is_unresolved_not_invented`; `test_missing_installed_contract_blocks_survivor`; `test_manifest_backed_technical_metadata_waits_for_exact_artifact_pin`; `test_strict_mode_fails_after_reportable_unresolved_routes`; `test_two_artifacts_become_two_pinned_about_refs_in_source_id_order`; `test_batch_with_no_artifact_in_input_is_unresolved_by_name`; `test_derived_artifact_sharing_the_batch_id_is_not_a_join_subject`; `test_reshape_from_batch_to_artifacts_is_reported_on_the_route`; `test_label_about_an_artifact_keeps_one_pinned_about_ref`; `test_batch_label_waits_for_the_artifact_pin` | translate | Planned | Keep exhaustive routing, exact pins, and no invention under Archive vocabulary (ADR 0015, INV-24). |
| `test_adoption.py`: `test_decision_does_not_invent_approval_or_revision_pin` | rewrite | Planned | Replace V2 decision semantics with assessment and transition claims (S-8, ADR 0003). |
| `test_gates.py`: `test_matching_counts_are_not_unexplained`; `test_an_unexplained_difference_is_reported_and_blocks`; `test_a_difference_matching_the_expected_count_has_zero_residual`; `test_a_difference_that_does_not_match_the_expected_count_still_blocks`; `test_every_expected_differences_domain_key_is_actually_reachable` | translate | Planned | Preserve count reconciliation and block unexplained loss (ADR 0015). |
| `test_gates.py`: `test_zero_case_is_zero_for_every_domain`; `test_song_performance_different_selection_successor_counts_negative`; `test_song_performance_same_selection_successor_contributes_nothing`; `test_audio_segment_different_bounds_successor_counts_negative`; `test_audio_segment_same_bounds_pair_must_show_zero_residual`; `test_transcript_with_only_rejected_successors_counts_positive`; `test_transcript_with_an_active_successor_contributes_nothing`; `test_transcript_about_a_same_bounds_retired_segment_counts`; `test_transcript_about_a_different_bounds_retired_segment_does_not_count`; `test_transcript_counted_once_when_both_rules_apply`; `test_transitive_two_hop_same_bounds_chain_still_counts`; `test_a_rejected_successor_disagrees_between_the_two_rules`; `test_an_active_successor_agrees_between_the_two_rules`; `test_no_rows_is_zero_not_a_crash` | defer | Planned | They encode unsettled current rules. |
| `test_gates.py`: `test_equal_counts_and_a_passing_check_is_a_pass`; `test_equal_counts_and_a_skipped_check_is_still_a_pass`; `test_unequal_counts_blocks_even_with_a_passing_check`; `test_a_blocked_check_blocks_even_with_equal_counts`; `test_a_missing_spec_is_reported_not_raised`; `test_a_crashing_gate_is_recorded_as_error_not_raised`; `test_main_all_exits_1_not_2_when_a_gate_crashes`; `test_gate_backup_restores_with_equal_counts`; `test_gate_backup_detects_a_restore_missing_a_record` | translate | Planned | Preserve independent fail-closed gate reporting and isolated restore (S-1, S-5, INV-24). |
| `test_gates.py`: `test_a_row_sourced_from_a_compared_schema_resolves_its_domain`; `test_a_row_with_no_recognised_source_schema_falls_back_to_shape`; `test_a_row_with_no_shape_and_no_source_is_unresolvable`; `test_schema_directory_names_exactly_the_compared_schemas`; `test_compared_schemas_is_exactly_the_schemas_disposition_routes_to_a_v2_shape`; `test_a_non_shape_route_never_reaches_current_records`; `test_matching_counts_pass`; `test_a_view_count_that_disagrees_with_domain_current_blocks`; `test_a_missing_view_count_key_is_treated_as_a_mismatch_not_a_crash`; `test_only_the_reviewed_label_counts_as_current`; `test_a_superseded_transcript_counts_once_not_twice`; `test_an_empty_copy_counts_zero_for_every_lifecycle_schema`; `test_lifecycle_schemas_matches_production_declaration_exactly`; `test_gate_current_passes_when_production_and_v2_agree`; `test_gate_current_blocks_on_an_unexplained_production_only_record`; `test_gate_views_reconciles_view_link_counts_against_domain_current`; `test_gate_views_reports_blocked_when_project_fails`; `test_gate_all_reports_gate_4_blocked_and_still_produces_the_other_gates_when_project_fails` | defer | Planned | Layout/view/current semantics are open. |
| `test_gates.py`: `test_a_four_field_tab_separated_line_parses`; `test_a_malformed_line_is_dropped_not_guessed` | translate | Planned | Keep strict plan parsing below the future plan schema (INV-24). |
| `test_gates.py`: `test_zero_drops_is_a_pass`; `test_one_drop_blocks_and_names_the_count`; `test_gets_are_grouped_by_repo_and_group`; `test_an_empty_plan_is_a_pass_with_no_gets`; `test_gate_retention_reports_an_empty_plan_as_a_pass`; `test_gate_retention_passes_when_the_fake_plan_has_no_drops`; `test_gate_retention_blocks_when_the_fake_plan_has_a_drop` | defer | Planned | Policy grammar, freshness, and failure domains are open. |
| `test_placement.py`: `test_four_named_cases_match_the_real_binary`; `test_no_dot_has_no_extension`; `test_a_too_long_single_component_is_dropped`; `test_only_the_last_two_dot_components_are_ever_considered`; `test_a_too_long_last_component_drops_the_whole_extension_even_if_earlier_is_valid`; `test_non_alphanumeric_extension_characters_are_rejected`; `test_builds_the_sha256e_key` | keep the mechanism | Planned | Retain measured git-annex SHA256E behavior (ADR 0009). |
| `test_placement.py`: `test_d1_path_rule` | defer | Planned | Layout is open. |
| `test_placement.py`: `test_a_valid_head_is_planned_with_the_expected_key`; `test_a_bad_digest_is_refused_not_guessed`; `test_an_uppercase_digest_is_refused`; `test_a_missing_size_is_refused_not_guessed`; `test_plan_never_reads_a_url_field_it_does_not_already_have` | translate | Planned | Preserve evidence-derived planning and fail-closed inputs (INV-24). |
| `test_placement.py`: `test_a_derived_artifacts_draft_keeps_its_derived_from_pin`; `test_no_contracts_on_the_head_means_no_contracts_key`; `test_plan_reports_expected_keys_including_the_derived_artifact`; `test_plan_is_empty_on_an_archive_with_no_artifacts`; `test_apply_places_and_admits_both_artifacts`; `test_apply_refuses_when_the_predicted_revision_lineage_is_gone`; `test_apply_with_only_refused_rows_does_nothing` | rewrite | Planned | Use Archive artifacts, contracts, transitions, stale-plan checks, and reinject (S-8, S-12, ADR 0015). |
| `test_step2_vertical.py`: `test_exact_draft_shape_counts`; `test_legacy_placeholder_performance_and_revision_rows_stay_unresolved`; `test_import_batch_routes_to_git_not_an_entity`; `test_coverage_observation_routes_to_git_not_a_claim`; `test_every_policy_decision_routes_to_git`; `test_job_attempt_provenance_stay_off_the_four_shapes`; `test_original_artifact_occurrence_is_unmanaged_with_joined_name`; `test_adapter_artifact_occurrence_name_is_last_path_component`; `test_derived_artifact_chain_pins_derived_from`; `test_musical_analysis_about_selection_input_artifact`; `test_feature_timeline_about_artifact_directly`; `test_transcript_v2_has_pinned_about_and_two_inputs`; `test_publication_about_selection_input_derived_clip`; `test_source_metadata_four_adapters_observed_and_kind`; `test_purpose_label_on_the_batch_reshapes_onto_its_artifact`; `test_label_about_an_artifact_keeps_one_pinned_about_ref`; `test_generic_batch_edge_is_reported_and_omitted_again`; `test_technical_metadata_emits_with_only_its_own_artifact_pin`; `test_reference_survival_has_zero_unresolved_refs`; `test_loop_stays_within_the_round_bound_and_reports_rounds`; `test_admission_order_matches_canonicalization_order`; `test_a_draft_that_drifts_between_rounds_is_never_admitted`; `test_real_door_admits_the_whole_fixture` | rewrite | Planned | Rebuild the full vertical path with Archive envelopes, exact provenance, JCS, and explicit unresolved routes. |
| `test_step3_continuity.py`: `test_lr_name_parses_capture_at_track_and_take`; `test_mic_name_parses_a_different_track`; `test_rollover_numbered_suffix_is_not_this_grammar`; `test_lowercase_extension_does_not_match`; `test_non_recorder_name_does_not_match`; `test_invalid_calendar_date_does_not_match`; `test_missing_track_suffix_does_not_match`; `test_true_positive_capped_and_within_gap_chains`; `test_size_only_false_positive_two_short_files_seconds_apart_do_not_chain`; `test_gap_only_false_positive_capped_file_followed_30s_later_does_not_chain`; `test_gap_boundary_is_inclusive`; `test_just_past_the_gap_boundary_does_not_chain`; `test_missing_capture_time_or_duration_never_chains` | keep the mechanism | Planned | Pure deterministic detector tests make no archival claim (ADR 0006). |
| `test_step3_continuity.py`: `test_three_parts_chain_into_one_recording`; `test_tracks_never_chain_into_each_other`; `test_same_capture_time_breaks_ties_on_artifact_id`; `test_part_with_no_capture_time_is_its_own_recording`; `test_part_with_no_duration_is_its_own_recording`; `test_non_own_capture_artifact_is_never_mentioned`; `test_technical_metadata_pinning_a_stale_revision_is_skipped_by_name`; `test_unparseable_duration_is_skipped_by_name`; `test_technical_metadata_value_field_is_not_used_to_find_the_claim`; `test_cli_on_records_file_writes_report_and_drafts`; `test_cli_reads_stdin_by_default`; `test_cli_rejects_both_rec_and_manifest` | translate | Planned | Preserve deterministic reporting and fail-closed pins without admitting a contract. |
| `test_step3_continuity.py`: `test_exact_recording_and_skip_counts`; `test_skipped_reasons_are_exact`; `test_two_part_lr_chain_offsets_and_gap`; `test_lr_and_mic_recordings_of_the_same_take_are_paired`; `test_one_part_recordings_have_no_second_part`; `test_gap_condition_keeps_capped_takes_separate`; `test_recording_ids_are_deterministic_and_distinct`; `test_draft_value_shape_matches_d2`; `test_draft_about_refs_are_exactly_the_distinct_part_pins`; `test_no_manifest_means_no_contracts_field`; `test_manifest_citation_is_attached_when_present`; `test_real_door_admits_the_recording_and_a_selection_about_the_same_pins` | defer | Planned | Durable continuity contract/current semantics are open. |
| `test_step3_performance.py`: `test_exact_draft_counts`; `test_annotation_value_shape`; `test_annotation_has_no_input_ref_to_the_artifact`; `test_import_batch_edge_is_reported_and_omitted`; `test_annotation_waits_for_the_selection_pin`; `test_two_segment_performance_is_refused_not_minted`; `test_supersedes_performance_id_is_retained_and_reported_not_a_real_edge`; `test_same_selection_pair_shares_object_id_and_pins_lineage_order`; `test_same_selection_pair_report_text_is_translated_as_a_supersedes_edge`; `test_same_selection_successor_with_its_own_revision_extends_the_lineage`; `test_cycle_between_two_same_selection_performances_is_unresolved`; `test_chain_crossing_selections_partway_is_unresolved`; `test_differing_revision_emits_and_equal_revision_folds`; `test_emitted_revision_shares_the_performance_object_id_and_chains`; `test_performance_with_no_emitted_revision_supersedes_nothing`; `test_missing_predecessor_row_is_unresolved_by_name`; `test_differing_head_unresolves_the_whole_subjects_revisions_not_one` | translate | Planned | Preserve performance-to-annotation mapping, explicit lineage, and no invention (ADR 0003, ADR 0005, INV-24). |
| `test_step3_performance.py`: `test_real_door_admits_the_fixture_predicted_equals_admitted` | rewrite | Planned | Re-target Archive Door/JCS. |
| `test_step7b_selection_chains.py`: `test_exact_draft_counts`; `test_same_bounds_pair_shares_object_id_and_pins_the_edge`; `test_same_bounds_pair_report_text_is_translated_as_a_supersedes_edge`; `test_different_bounds_pair_keeps_separate_object_ids_and_no_edge`; `test_absent_target_is_provenance_only_and_reported`; `test_three_member_chain_shares_object_id_root_to_tip`; `test_reviewed_successor_over_unreviewed_target_is_not_a_chain`; `test_cycle_between_two_same_bounds_segments_is_unresolved`; `test_chain_crossing_bounds_partway_is_unresolved`; `test_chain_crossing_review_state_partway_is_unresolved_by_crossing_not_missing_pin`; `test_draft_count_equals_reviewed_segment_count`; `test_real_door_admits_the_fixture_predicted_equals_admitted` | defer | Planned | Every assertion depends on unsettled selection/current semantics. |
| `test_supersedes_chains.py`: `test_chain_members_share_one_object_id_root_to_tip`; `test_root_has_no_supersedes_successors_pin_their_predecessor`; `test_admission_order_is_root_then_middle_then_tip`; `test_broken_chain_predecessor_missing_stays_unresolved_never_admitted`; `test_cycle_is_unresolved_not_an_infinite_loop`; `test_chain_crossing_a_different_selection_is_a_named_contract_violation`; `test_musical_analysis_chain_shares_object_id_and_pins_supersedes`; `test_feature_timeline_chain_shares_object_id_and_pins_supersedes`; `test_label_chain_shares_object_id_and_pins_supersedes` | translate | Planned | Preserve stable identity, exact lineage, and cycle refusal (S-9, S-10, INV-24). |
| `test_supersedes_chains.py`: `test_chain_admits_tip_is_domain_current_root_and_middle_are_not`; `test_a_rejected_successor_retires_the_tip_instead_of_reviving_it` | defer | Planned | They decide current/rejection behavior. |
| `test_translate.py`: `test_threshold_equality_does_not_stop`; `test_artifact_loss_is_explicit_without_fabricating_annex_fields`; `test_every_source_field_has_a_disposition`; `test_nested_declared_refs_and_revision_pins_preserve_input`; `test_named_preset_uses_its_record_id_for_mapping`; `test_entity_plus_claim_is_flat_named_data`; `test_declared_subject_and_supersession_are_rewritten`; `test_missing_subject_is_actual_no_fit_without_fabricated_entity`; `test_orphan_event_keeps_event_identity_and_batch_gap_as_loss`; `test_native_gap_is_loss_and_reported_separately`; `test_policy_decision_stays_a_scoped_claim`; `test_current_selection_reports_slice_extra_draft`; `test_direct_unix_draft_is_clean` | delete | Planned | The entire lossy translator is superseded by adoption accounting. |
| `test_vertical.py`: `test_artifacts_get_predicted_revisions`; `test_metadata_about_ref_is_pinned_and_value_matches`; `test_reviewed_segment_becomes_pinned_audio_selection`; `test_suggested_segment_is_generated_not_a_draft`; `test_report_additions_are_present_and_consistent`; `test_admission_predicted_equals_admitted_and_batch_order`; `test_admission_mismatch_is_detected_and_listed`; `test_artifact_missing_storage_uri_blocks_its_dependents`; `test_door_canonicalization_failure_demotes_artifact_and_its_dependents`; `test_fake_reject_marker_is_honoured_by_the_fake_door`; `test_end_before_start_is_unresolved_naming_the_invariant`; `test_string_bound_is_unresolved_naming_the_field`; `test_negative_start_is_unresolved`; `test_missing_operator_is_unresolved_never_defaulted`; `test_boundary_evidence_note_equal_to_source_id_is_not_rewritten`; `test_no_draft_ever_has_song_occurrence_kind`; `test_supersedes_segment_id_stays_in_provenance_and_is_reported`; `test_real_door_predicted_equals_admitted` | rewrite | Planned | Preserve the vertical invariants under Archive Door, exact boundaries, and JCS. |

## Phase accounting

All source files, module sections, commands, record shapes, named test methods,
migration steps, fixtures, and packaging pieces in the V2 spike have a row or
an explicitly exhaustive grouped row above. No unit is silently unaccounted
for. Phase 2 may touch only rows whose reason cites settled authority and must
leave every `defer` row unsupported.
