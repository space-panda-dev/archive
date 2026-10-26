---
title: "Separate enrichment responsibilities inside Archive"
subtitle: "A workflow-ready implementation plan with compatibility gates and adversarial review"
form: plan
for: "The operator and the implementation workflow"
status: "Proposed plan; architecture requires an accepted ADR before implementation"
date: 2026-10-25
---

# Keep enrichment in Archive and separate its responsibilities

::: verdict
Keep the feature bank and enrichment in this repository. Establish a small internal interface between Archive-specific selection and claims, feature production, and execution resources. Make requested results compose through declared proposer requirements and shared flow configuration. Reuse the bank, runner, scheduler, and Spool transport already built. A second repository, generic workflow engine, and a new policy language are outside this plan.
:::

The operator asked for a detailed plan and adversarial judgment, not implementation. This document authorizes no runtime changes, deployments, policy admission, or acceptance of a decision record. The future workflow must obtain acceptance of its concrete proposed ADR before changing architectural interfaces. Characterization tests and an implementation inventory can precede that acceptance.

The word “flow” below describes a selected set of enrichment results and its existing intake behavior. It introduces no durable Archive entity. Proposed Python names are implementation sketches, not accepted vocabulary or public contracts.

# The evidence supports internal separation

The extractor interface already declares commands, parameters, accepted media, dependencies, preferred inputs, and outputs. Catalog validates required outputs and cycles; the runner validates results before filing; Bank owns lookup and cache identity. Preserve this depth rather than wrapping each call in a new framework.[^extractors]

The composition decisions are dispersed. Capture has a fixed procedural chain and extraction TARGETS; backfill has configurable units, a separate proposer selection, resource settings, and region planning. Proposer declarations expose id, version, and a function, while input requirements live inside those functions. Adding an extractor is supported; connecting a desired claim to the work needed to produce it is less local.[^composition]

Code already supports region fallback: a proposer lacking whole-recording features is tried through RegionView over available regions. Earlier README and MAP wording saying readers never use region entries is stale. The implementation inventory must determine the precise support of each proposer and relay reader rather than generalizing from this finding.[^regions]

Policy already has an accepted direction: derivation desires name claim kinds, configuration maps kinds to tools, and policy evaluation reads evidence rather than World or the feature bank. This work supplies a mechanism seam for that future caller; it does not implement policy evaluation.[^policy]

# Ownership is explicit

| Owner | Owns | Interface to the next responsibility |
|---|---|---|
| Archive selection and claims | Reading Artifacts and stream claims; curated and suggested song regions; claim-kind-to-method mapping; proposer logic; Door proposals; World refresh | Content references, explicit regions, selected methods, and explanations of selection |
| Feature production | Extractor Catalog, dependency closure, input choice, cache lookup, request/result validation, region identity, provenance | Feature requests and validated bank entries or explicit failures |
| Existing execution mechanism | Resource reservations, local process execution, scheduling priorities, Spool submission and collection | Runs already-described feature work within configured capacity |
| Fleet and Spool outside Archive | Host configuration, installed model environments, offered capacity, opaque work transport | Configured programs and transport capabilities; no Archive record knowledge |

Archive retains derivation desires. Fleet owns host lifecycle. Archive's existing scheduler retains resource-aware job placement; this plan does not transfer it into Fleet or make Fleet interpret Archive claims. The implementation should preserve the current local and remote adapters rather than add a second scheduler.[^boundaries]

## Classify every new piece of state

- Admitted claims, derivation runs, and parameters Artifacts remain evidence, written only by Door.
- Feature entries and derived media remain regenerable. Losing the bank must not lose admitted history.
- Expanded plans, dispatch bookkeeping, retry/failure records, and diagnostic reports are operational. Their loss permits replanning; none becomes authoritative completion state.
- Flow configuration is source/operator configuration until a future accepted policy mechanism says otherwise. A saved plan is never an authoritative policy revision.

INV-2, INV-4, INV-9, INV-10, INV-16 through INV-19, INV-22, INV-23, and INV-24 govern the work. An invariant conflict stops implementation and produces a proposed decision rather than a hidden exception.[^invariants]

# Use a small shared interface

## Declare requirements beside proposers

Extend the existing proposer registry with internal typed requirement data. Each proposer must expose its produced claim contracts, supported scopes, and features it may read. Distinguish mandatory input groups, ordered alternative groups, optional enhancements, and input preferences already owned by extractor manifests. Include extractor-on-file-feature requirements such as measuring stems/bass. A declaration of availability must not promise a successful claim: an available feature can contain no usable observations.

Start with a bounded representation using ordinary Python data and functions. Enumerate the real requirements of all registered proposers, including the explicitly unsupported timed-lyrics path. Avoid arbitrary predicates, executable configuration, a generic expression interpreter, or an external plugin loader. If existing alternatives cannot fit a small representation, stop and reduce scope or amend the proposed ADR.

Make the declared requirements the source used for preflight and planning. Use one shared input resolver for preflight and proposer execution. The proposer consumes the resolver's selected entries and records the entries it actually reads; it does not independently repeat the choice algorithm. Instrumented bank-read tests verify this connection and catch undeclared optional reads, but are not a substitute for shared resolution. A second hand-maintained dependency graph is not completion.

Optional reads must be declared and recorded when used. Strict needs cause dependency production only for a result explicitly selected for production; an opportunistic proposer attempt never expands production work. A preferred input remains opportunistic unless the flow explicitly requests its producer: ADR 0076 says a preference alone never makes a stem. Alternatives are ordered choices, not instructions to compute every possible alternative. Preserve each proposer's current choice order before adding any new selection behavior.

## Share result selection without erasing flow differences

A flow's configuration distinguishes three selections: results to produce (expand their prerequisites), proposers to attempt using whatever features are available (produce no prerequisites), and explicit feature-only targets. Results to produce select supported claim kinds and configured proposer methods. Configuration maps a claim kind to one or more proposer ids because competing methods may legitimately produce the same contract. Use contract identity and supported version constraints, with accepted compatibility rules; do not infer equivalence from display names.

Make capture and backfill call the same expansion function, with different explicit selections that preserve their characterized behavior. Shared definition means one representation and one resolver, not identical defaults. Preserve capture's four default proposers and its mix-only extraction targets; bass may remain unavailable until stems exist. Preserve backfill's broader proposer and extractor set. Translate capture into its current explicit mix feature targets plus opportunistic default proposer attempts. Translate backfill into its current explicit units plus opportunistic configured proposer attempts. Neither translation infers new production desires from a proposer list. Retain feature-only targets without immediate claim consumers when required for compatibility. New results-to-produce selections are opt-in, additive configuration reviewed in the ADR; they must not silently reinterpret --proposers.

Expansion resolves only results-to-produce and explicit feature targets into required extractor/source pairs and prerequisite work using Catalog. Opportunistic attempts resolve their available inputs for diagnosis and execution but enqueue no prerequisites. It produces missing work and explicit reasons, not a second job-execution graph. Backfill's Planning/Runner remain the owners of its job graph, follow behavior, priorities, and resource control. The runner remains the owner of request/result validation and atomic filing. Reuse bank key and input-selection functions; never implement parallel key calculation.

A suitable internal call has these conceptual inputs and outputs:

| Input | Output |
|---|---|
| Validated selection and configured method mapping | Selected proposers and explicit feature-only targets |
| Catalog and effective extractor parameters | Validated dependency expansion using existing identities |
| Content digest, media type, location, explicit source/region | Work targets ready for the existing runner or planner |
| Current bank observations and runtime capabilities | Cache hits, missing work, deferred choices, blocked reasons |

For input alternatives, separate observation-based choice from production choice. First resolve among validated available entries using the proposer's existing choice rules, even if a lower-ranked alternative is cached. If an explicitly requested result is missing required entries for every available path, choose the first declared production-capable alternative with configured prerequisites, respecting existing held-failure rules. If none qualifies, report blocked. A failed selected production path stays failed; automatic fallback to a different producer requires an explicitly declared existing quality-fallback rule or a separate approved behavior change. A successful empty measurement remains a valid empty outcome and does not trigger speculative alternative computation. Optional enhancements are read when available and produced only when explicitly requested. Gate A must approve this truth table before enabling results-to-produce for any complex method. A method without shared resolution can remain an explicitly labeled opportunistic attempt, but its automatic expansion must fail as unsupported rather than guess.

Keep archive records, Door, World, and archive discovery out of the feature-production interface. Existing extract.py already satisfies much of this separation; modify it only where evidence shows a leak. Avoid a directory reorganization or package move without a specific caller benefit.

## Explain the plan and revalidate execution

Extend the existing backfill dry-run/report shape instead of adding a parallel command family. Report, per selected result: why selected; producing method; required features; content and scope; effective parameter/key identity where known; input alternatives; cache/equivalence/coverage decision; dependencies; configured route; estimated cost and its source; and blocking reason. For capture, expose the enrichment portion through the shared planner without promising that unknown future detections or matches can be fully planned.

Use existing status conventions when possible. Distinguish missing features, an unconfigured interpreter, held previous failures, unsupported behavior, damaged entries, unavailable transport, and a valid empty analysis. Unknown cost is unknown, never zero. Diagnostics must explain the causal chain from selected result to blocked prerequisite.

Dry runs may read current validated state and invoke existing read-only discovery, but start no extractor or remote task and admit no evidence. Preferred inputs can change after prerequisites run: describe unresolved choices as deferred, then re-resolve through the runner before dispatch. A dry run is not a prediction of feature values.

Include the evidence revisions used for selection, content digest, Catalog versions, effective parameters/configuration, and any policy identity supplied by a future policy caller in the diagnostic plan. Before extractor dispatch, check content identity, current bank hit/coverage/equivalence, effective parameters, and program/route availability using existing runner/planner checks; re-resolve deferred preferred inputs there. Before proposal construction, refresh the archive through the caller's existing reader path, pin the Artifact/stream revisions and actual feature entries used, then rely on existing dry-run/admission validation for archival state. These are per-step checks, not a transaction across the full chain. Preserve today's follow/rescan timing for changed selections; no immediate cancellation of running extraction on curation edits is promised. If audit finds a required content/configuration check missing, implement that bounded check or propose the stronger guarantee separately. A newly filled cache can safely eliminate work. Saved reports are explanatory unless an accepted ADR deliberately introduces an apply interface; this plan does not add one implicitly.

# Execute through modules with explicit gates

## Work package A — architecture and compatibility inventory

Owner: the integrating workflow agent. Read the source pointers in this document, repository MAP, relevant ADRs, vocabulary, and open questions. Recheck live operational claims with wf observe rather than treating documented measurements as current.

Deliver a concrete proposed ADR under the next available decision number, with an index entry. Cover ownership, interface direction, requirement semantics, flow configuration ownership, method selection, diagnostics, compatibility, and state classification. Keep existing unresolved policy-language and capture-recovery questions open. Record the precise per-step freshness checks and their implementing functions; distinguish them from stronger transaction guarantees not provided here. Approve an alternative-input truth table covering cached paths, unavailable producers, held failures, successful empty results, and prerequisites completed during the run. Propose any necessary amendment explicitly; never mark it accepted on the operator's behalf.

Inventory each registered proposer: produced contracts; mandatory, alternative and optional reads; selected content/stem; region support; fallback/empty semantics; current version; and caller defaults. Inventory extract.py, capture, backfill, region planning, feature-only readers, and remote dispatch. Record semantic import dependencies, not just module names: pure contract validation helpers may remain Archive-side even when imported from World.

Add targeted characterization cases for the actual caller defaults, including features made without immediate claims. Record representative cold/warm plans and normalized proposed payload/provenance through existing synthetic fixtures. Normalization may remove generated ids and timestamps only; it must retain input revisions, feature keys, sample positions, parameters, and method identity.

::: {.gate state=open unblocks="B and C"}
Gate A: architecture is reviewable and accepted

- [ ] Proposed ADR and inventory cover every registered proposer and existing caller.
- [ ] The operator has accepted the architectural decision; its acceptance is recorded.
- [ ] Existing behavior and deliberate differences between capture and backfill are executable expectations.
- [ ] No new policy semantics, extractor behavior, or archive contracts are bundled into the refactor.
:::

## Work package B — proposer requirements and shared resolution

Owner: proposer/selection module agent. Own proposer declarations, input-resolution helpers, and their focused tests. Coordinate interfaces with C through the accepted ADR; do not edit scheduler internals or capture concurrently.

Implement declarations for simple chord, beat, key, tuning, and section proposers first. Then cover bass, bass-line, and notes with their existing alternatives, optional reads, preferred inputs, and region semantics. Mark timed-lyrics explicitly unsupported with its existing missing prerequisites; registration must not turn it into an executable flow.

Introduce the smallest shared expansion function that feeds existing targets into the runner/planner. Validate unknown claim kinds/methods, unsupported versions and scopes, invalid feature references, file-feature requirements, ambiguous mappings, and cycles before work starts. Respect existing Catalog validation instead of duplicating it. Select both chord methods when current configuration requests both; never silently rank competing claims.

::: {.gate state=open unblocks="D"}
Gate B: requirements are executable and local

- [ ] All proposers are accounted for, including unsupported ones and optional reads.
- [ ] Registry-enumerating tests connect selection to the actual registered callable and bank reads.
- [ ] Supported selected results expand into the required work; unsupported ones explain why.
- [ ] Plain preference does not schedule stems; explicit stem requirements do.
- [ ] Existing payloads and provenance remain unchanged for equivalent inputs.
:::

## Work package C — feature-production and execution seam

Owner: bank/runner module agent. Own only proven coupling removals in bank.py, extract.py, remote.py, and supporting tests. If the inventory shows those interfaces already meet the ownership rule, preserve them and supply the verification rather than adding wrappers.

Keep feature work expressed in content digests, paths/media types, feature names, parameters, source placement, and regions. Preserve request/result formats, entry bytes, keys, equivalence records, covering-entry behavior, configured interpreters, process groups, cancellation, failure logs, and atomic placement. Preserve remote task payload and capability compatibility with deployed workers.

Prove feature production without an archive or Door executable, using synthetic content and an isolated runner environment. This tests an internal seam, not a hypothetical second product. Check actual execution as well as imports. Prevent Archive record discovery, Door admission, World refresh, and host lifecycle operations from entering this path.

::: {.gate state=open unblocks="D"}
Gate C: execution remains compatible

- [ ] Existing bank entries and operational request records still load and resolve.
- [ ] Existing remote protocol and capability names remain compatible.
- [ ] No extractor or proposer version bump is required for a behavior-preserving move; output-affecting changes are separated and versioned.
- [ ] Cold execution validates outputs; a warm rerun launches no process.
- [ ] Missing programs, corruption, timeouts, cancellation, and remote refusal remain explicit.
:::

## Work package D — integrate capture and backfill

Owner: capture/backfill integration agent, after B and C merge. Own caller configuration, compatibility adapters, selection expansion integration, and scheduler-facing translation. Preserve CLI flags, exit codes, report shapes, default targets, and execution order unless the accepted ADR explicitly approves an additive change.

Represent capture TARGETS and backfill's extraction/proposer selections through the shared configuration representation. Preserve feature-only targets and opportunistic attempts as distinct fields; only explicit results-to-produce cause automatic prerequisite expansion. Keep compatibility translation for existing backfill.units and --proposers configurations. Explicit units continue to work as feature-only targets. Define precedence concretely: existing explicit flags and units retain their current effect; new configuration must reject conflicting declarations rather than silently broaden work. Document the tested precedence table.

Preserve probe/detect/media/match ordering, primary-source selection, no-audio handling, and capture failure reporting. Share only the extract/propose portion initially. Backfill retains songs/eager/on-demand, request priority, retry-failed, usable-first gating, per-recording and batched proposals, follow rescans, local/remote overflow and capacity reservations. Feature/proposal deduplication remains based on existing keys and run identity.

When bank inputs or source evidence change during execution, use existing discovery/rescan timing and the concrete per-step checks specified above. Configuration remains frozen for a run as today; changing it requires a new run, not live reload. Do not let an optimistic dry-run choice become a stale dispatch decision. Keep proposals serialized as today; extraction concurrency is separate from Door changes.

::: {.gate state=open unblocks="E"}
Gate D: both callers exercise the shared mechanism

- [ ] Capture and backfill call the same expansion implementation; no shadow target list remains.
- [ ] Their deliberately different defaults match the A characterization cases.
- [ ] An explicit feature-only request still works without a proposer.
- [ ] Follow, batching, retries, resource limits and remote behavior pass their existing tests.
- [ ] Interrupted capture behavior remains unchanged; recovery stays an open decision.
:::

## Work package E — explanatory plans and extension guide

Owner: diagnostics/docs module agent, after D. Extend current reports additively with a documented structured explanation field; preserve existing event/status names. Keep parameter values and local content locations within existing private diagnostic surfaces; do not add a public route or embed private paths in repository fixtures.

Implement causal explanations, deferred choices, blocked reasons, and bound-input metadata described above. Provide documented synthetic examples for cold, cached, region-only, unconfigured, held-failure, and unsupported cases. Show both selected claim outputs and explicit feature-only outputs. Cost estimates cite configured/measured values and unknowns; no new measurements are invented.

Update adding-an-extractor, feature-bank format where its explanatory contract changes, enrichment README, MAP, and boundaries to match accepted interfaces. Correct stale region-reader claims precisely after checking actual readers. Add a contributor recipe for adding an extractor, adding a proposer with requirements, composing a selection from existing methods, improving an algorithm with a version bump, and verifying the full caller-to-Door path.

::: {.gate state=open unblocks="F"}
Gate E: an operator can understand and extend a flow

- [ ] A blocked result explains its prerequisite chain and the appropriate next action.
- [ ] Unknown/deferred facts are labeled without falsely declaring work complete.
- [ ] Dry run invokes no extractor, remote submission, Door commit, or World mutation.
- [ ] A new selection using existing proposers needs no edit to capture.chain or Runner.
- [ ] Documentation agrees with the dispatched code and distinguishes proposal from supported behavior.
:::

## Work package F — adversarial acceptance and handoff

Owner: independent review agent, then integrator. Review the final diff against the accepted ADR and the A compatibility matrix. Trace one simple mix-based result and one region/stem-based result from caller selection through runner, bank read, proposer, Door admission, and World projection where already supported. Attack failures and expensive paths rather than only happy-path function tests.

Reject regressions in history quietness, input immutability, cache identity, failure visibility, input choice, disclosure controls, or supported worker compatibility. Require evidence that both callers reach the shared resolver and that actual proposer reads match declarations. Compare costs structurally using process-launch bombs and scheduled-work counts; use the existing benchmark harness only when performance behavior changes, with private data kept outside the repository.

::: {.gate state=open unblocks="Implementation complete"}
Gate F: the separation earns its complexity

- [ ] All acceptance cases below pass, plus existing suites for changed behavior.
- [ ] Existing CLI/configuration and worker compatibility is demonstrated.
- [ ] Selected outputs can be composed without a scheduler or capture-chain edit.
- [ ] Admitted evidence, cache identities, and warm reruns remain equivalent.
- [ ] No unresolved high-severity review finding remains, and documentation checks pass.
:::

# Test the risks through the real interfaces

| Case | Required observation |
|---|---|
| Cold mix-based selection | Only selected required work plus existing explicit feature targets; real registered proposers reached |
| Warm bank and unchanged archive | No extractor launch; no remote submission; no Door write; bank reader agrees with completion |
| Two chord methods | Both configured methods remain distinct claims and runs, with their own provenance |
| Capture cold bank with default bass attempt | Existing mix-only work unchanged; no stems, tonal-on-bass or other newly inferred jobs |
| Explicit bass result desire | Required stem path expands only under the opt-in results-to-produce selection |
| Preferred vocal stem missing | Mix fallback remains available; preference alone creates no separator job |
| Explicit stem-dependent selection | Producer and dependent measure ordered; content identity follows stem bytes and source placement |
| Optional enhancement missing/present | Valid base result without it; when used, its key and software appear in provenance |
| Alternative inputs | Existing choice order preserved; only the chosen production path runs, with deferred choice reported when needed |
| Region-only features | RegionView fallback, whole-first preference, exact sample shifts, and clamped claim bounds preserved |
| Covering/equivalent entry | Planner completion agrees with Bank; remote stand-in reuse follows validated equivalence |
| Corrupt or inaccessible entry | Explicit failure; never treated as a cache miss, empty feature, or successful completion |
| Missing interpreter or prior failure | Distinct blocked reason; retry behavior unchanged; unrelated supported work follows current semantics |
| Valid empty measurement | Distinct from missing input, unsupported method, or execution failure |
| Changed inputs after plan | Dispatch rechecks content, bank state and frozen effective configuration; proposals pin refreshed evidence; newly present cache avoids duplicate work |
| Cancellation or lost operational state | Child groups stop; partial output is not filed; restart replans without authoritative task history |
| Lost feature bank | Work recomputes; admitted evidence survives; equivalent rerun adds no duplicate history |
| Follow and usable-first | Requests promoted with prerequisites; background gating and proposal batching remain intact |
| Old configuration and worker | Existing units/flags and request payloads work; conflicts in new configuration fail explicitly |
| Unsupported timed lyrics | Preflight says unsupported and names missing prerequisites; no invented extractor runs |
| Dry run | Side-effect bombs on extractor launch, remote submission, Door commit and World refresh remain untouched |

Use existing tests as the baseline, extending the suites that own each behavior. Use synthetic audio and real Door-backed fixtures for end-to-end claims; a mocked writer cannot prove admission or provenance. Enumerate registries in requirement-coverage checks so a newly registered proposer cannot quietly escape validation.

# Run the workflow with bounded ownership

Workflow entry point: read this plan and MAP, run package A, and return its concrete proposed ADR and compatibility evidence for operator acceptance. Resume B through F only from the recorded acceptance and the merged A commit. At dispatch, supply the package letter, its owned files, accepted ADR path, predecessor commit, and the fixed handoff; keep the full plan available by pointer.

Dependency order: A → B and C → D → E → F. B and C may run in parallel only after A fixes their interface and file ownership. D owns integration conflicts. If C needs no implementation change, finish it with evidence rather than creating a new module. Shared files, documentation updates, and branch merges belong to one designated integrator.

Every concurrent editing agent gets its own wf worktree and branch. The orchestrator uses the agent-workshop multi-agent guide, checks current suite definitions with wf suites, measures module stages with wf meter, and invokes wf budget --label after each commit or test run. On HANDOFF it starts a fresh continuation from the committed handoff; no stage drops its Left items.[^workflow]

Run focused checks through wf check while iterating. Before each implementation commit run wf vet and wf check --tier 30 --changed. Run heavy suites on the laptop via the established wf facility. Start long checks once with --no-wait and wait using wf wait. Before a PR run applicable tier-300 checks; before merge run the repository's required wf check --tier night --changed --on laptop. The actual final suite set follows touched modules, including enrich/capture, remote/region tests, Door-backed integration, and relevant World checks. A NOT RUN is not a pass; name unavailable infrastructure in the handoff.

Each module handoff is at most 60 lines with Commit, Changed, Tests, Left. Include the accepted ADR, changed interface, exact validation summary, compatibility status, and unresolved questions. The orchestrator preserves provenance of commits and never pushes commits it did not make. Runtime environments remain read-only: implementation agents never deploy, restart, activate, or provision hosts.

## Bound the size and preserve rollback

Use one coherent compatibility-preserving commit per module slice, with tests. Establish the simple proposer path before complex stem/region declarations. If meeting the interface requires a general predicate language or second scheduler, stop at the last green commit and bring a reduced design to the operator. Do not expand the ADR by implementation.

No bank-format or extractor-wire-format migration is planned. Existing entries must stay usable. If identity or serialized format must change, split that into a separate versioned proposal before continuing. Rollback reverts code/configuration commits without deleting bank entries or reverting admitted evidence; new evidence remains historical evidence. Never roll back by deleting archive records.

# Decisions and exclusions keep the plan bounded

The operator's acceptance of the ADR must settle the requirement representation, configuration location and precedence, claim-kind mapping/version constraints, and additive diagnostic shape. The implementation inventory supplies concrete examples for those choices. This document proposes their semantics but does not approve an arbitrary future implementation.

Capture recovery after a partly completed import remains open. Cross-content feature dependencies, forced alignment inputs and models, decoded-audio sharing, and spectral-peak sharing remain their existing open questions. Any chosen extension beyond current behavior gets a separate decision and versioning review.

The completed work includes internal separation, declared proposer requirements, shared result expansion, preserved caller differences, explanatory dry runs, bound-input revalidation, end-to-end verification, and an extension guide. It excludes a repository split, new consumer, full policy evaluator, compute provisioning, general workflow engine, new public service, algorithm retuning, or automatic capture recovery.

# Adversarial judgment

::: risk
The weakest assumption is that proposer requirements can become declarative without reproducing the proposer's algorithm. Notes and bass already use alternatives, optional context, and input-dependent fallbacks. A generic requirement language could cost more than today's explicit lists and still disagree with execution.
:::

The strongest alternative is narrower: keep capture and backfill explicit and extract only their duplicated target-selection data. That is preferable if a shared resolver cannot represent the existing methods with a small bounded model.

The cheapest falsification is the A inventory followed by B's simple and complex proposer slices. Require actual reads to agree with requirements, preserve preferred-input semantics, and show that composing a selection removes a caller edit. If these fail, reduce the design before integrating.

This draft incorporates three defenses: a second repository is deferred; no second scheduler is introduced; capture and backfill keep distinct defaults. Independent review found two high-severity issues in the initial draft: expanding every selected proposer would make capture produce stems, and allowing test-only agreement could preserve two input-choice algorithms. Both are addressed: opportunistic attempts are separate from results-to-produce, compatibility translations retain existing work, and shared resolution is mandatory for automatic expansion. The reviewer also found ambiguous alternative-production semantics and overly broad freshness promises; the plan now specifies a choice truth table and concrete per-step checks without transactional guarantees.

Verdict: proceed with A and the bounded resolver slices, conditional on operator acceptance of the concrete ADR. If complex methods cannot share resolution without an interpreter, keep them opportunistic and explicitly unsupported for automatic expansion, and report that reduced delivery rather than declaring full automatic coverage complete. The narrower shared-configuration change remains a valid fallback. An accepted ADR does not substitute for compatibility evidence.

# Source pointers

[^extractors]: [Adding an extractor](../adding-an-extractor.md); [extractor contract ADR 0074](../decisions/0074-extractor-contract.md); [Catalog and Bank](../../enrich/bank.py); [runner](../../enrich/extract.py); [feature-bank format](../feature-bank.md).
[^composition]: [capture chain](../../capture/capture.py); [capture extraction step](../../capture/propose_step.py); [backfill](../../enrich/backfill.py); [proposer registry](../../enrich/proposers.py); [propose command](../../enrich/propose.py).
[^regions]: [proposer region execution](../../enrich/propose.py); [region selection](../../enrich/regions.py); [preferred inputs ADR 0076](../decisions/0076-preferred-inputs.md); [bulk import ADR 0112](../decisions/0112-bulk-import-at-scale.md).
[^policy]: [policy ADR 0007](../decisions/0007-policy.md); [plan/apply ADR 0008](../decisions/0008-plan-and-apply.md); [open questions](../open-questions.md).
[^boundaries]: [boundaries](../boundaries.md); [remote execution](../../enrich/remote.py); [backfill queue](../../enrich/backfill_queue.py); [backfill proposals](../../enrich/backfill_proposals.py).
[^invariants]: [invariants](../invariants.md); [state model](../state-model.md); [provenance](../provenance.md).
[^workflow]: Agent-workshop README, section 4, “Writing a multi-agent workflow”; repository AGENTS and MAP govern the executing checkout. Locate the installed guide rather than copying its implementation into Archive.
