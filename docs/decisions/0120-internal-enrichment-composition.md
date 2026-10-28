# 0120. Enrichment composes inside Archive through declared proposer inputs

Status: accepted (operator, 2026-10-26). The operator reviewed and accepted this
concrete record after the Gate A inventory and adversarial review.

## Context

The approved [plan](../proposals/enrichment-separation-plan.md) keeps enrichment
in Archive. There is no established second consumer for a separate project.
Capture and backfill already differ deliberately: capture makes mix features
and opportunistically proposes four methods; backfill selects broader work,
regions, scheduling priorities and nine methods. Their selections are separate
from the feature requirements currently embedded in proposer functions.

The [compatibility inventory](../proposals/enrichment-compatibility-inventory.md)
records those requirements. ADRs 0074 and 0076 already give Catalog and Bank
ownership of extractor dependencies, keys and preferred inputs; ADR 0090 owns
Spool transport; ADR 0112 owns backfill regions and scheduling. Deepen those
modules rather than add another execution graph. INV-2, INV-4, INV-9,
INV-10, INV-16, INV-17, INV-18, INV-19, INV-22, INV-23 and INV-24 remain unchanged.

## Decision

### Ownership and placement

Keep Archive discovery, stream/Artifact pins, song selection, claim construction,
Door admission and World refresh Archive-side. Bank, Catalog, extract.Run and
remote retain content digests, file features, explicit regions, effective
parameters, validation, reuse and placement. Existing backfill Planning/Runner
retain priorities, resource reservations, follow intake and remote routing.
Fleet owns host lifecycle; Spool carries opaque work. No package move is needed.

Add one internal Python module, `enrich/composition.py`, imported by capture
and backfill. Requirement declarations and method-specific input resolution
live beside their callable in `enrich/proposers.py`; no external plugin loader.
These Python identifiers are implementation names, not new archival nouns.

The finite representation is:

```python
FeatureTarget(extractor: str, source: str | None = None)
ResultTarget(contract: tuple[str, str], methods: tuple[str, ...])
Selection(produce: tuple[ResultTarget, ...],
          attempt: tuple[str, ...], features: tuple[FeatureTarget, ...])
FeatureRead(extractor: str, outputs: tuple[str, ...],
            source: str | None = None)
InputPath(id: str, required: tuple[FeatureRead, ...],
          production: tuple[FeatureTarget, ...] | None)
Requirement(contracts: tuple[tuple[str, str], ...],
            scopes: tuple[str, ...], paths: tuple[InputPath, ...],
            optional: tuple[FeatureRead, ...], resolver: str)
```

Each Proposer gains a Requirement. `source=None` means mix; a string names a
file feature such as `stems/bass`. Paths are ordered alternatives; every read
within one path is mandatory. `production=None` explicitly disables automatic
production for that path. Optional reads never expand work. Scopes are the
closed strings `whole` and `region`. For example, beats declares contract
`archive.claim/meter_layers` version `2`, both scopes, one path requiring
`FeatureRead("rhythm", ("pulse", "downbeats"))`, production target
`FeatureTarget("rhythm")`, and no optional reads. Bass declares same-stem
paths containing `FeatureRead("stems", ("bass",))`,
`FeatureRead("tonal", ("bass_chroma", "pitched"), "stems/bass")`, and mix
rhythm/pulse. Its production path is enabled only after the recipe tests below pass.
Resolver ids are a closed internal set:
`direct`, `bass`, `bass-line`, `notes`, `unsupported`. `direct` selects its sole declared path; the three specialized recipes select
among the declared paths and optional references from the inventory. Each
conditional read appears in declaration data, including tuning, tempo and bleed
inputs; the recipe implements selection conditions, not a hidden second list
of feature dependencies. This is ordinary
Python data plus named functions, not a predicate language or interpreter.

The shared callable is `resolve_inputs(proposer_id, context) -> ResolvedInputs`.
It returns selected entries with output names, source placement, optional reads,
missing reasons and selected recipe path. Both preflight and proposer execution
use it. Execution reads these selected entries instead of repeating Bank choice
logic. Proposal.features still records actual reads, including conditional reads;
resolution itself does not pretend every possible optional read was consumed.

`expand(selection, context, catalog, parameters, capabilities) -> Expansion`
returns ordered feature targets, selected proposer ids and causal diagnostics.
It calls Catalog for dependency validation and Bank for identity/input choice;
it neither calculates a parallel key nor schedules jobs. Existing caller adapters
convert targets to extract.run_default/run_from or backfill Unit/Planning.want.
Expansion receives no archive reader, Door command or World store. Context here
is the existing proposer Context, containing bank, digest and stream sample
bounds, not an Artifact record. Capabilities are observed configured program and
route availability, not resource provisioning instructions.

### Configuration and precedence

Use the existing extractor JSON config selected by capture `--extract-config`
and backfill `--config`; add an optional top-level `composition` object.
`extract.load_config` validates its outer shape and ignores its meaning during
standalone execution. `composition.py` validates its contents before work starts.

```json
{"composition": {"capture": {
  "produce": [{"contract": ["archive.claim/key_regions", "1"],
               "methods": ["key"]}],
  "attempt": ["chords-jiang", "chords-chordino", "beats", "bass"],
  "features": [["chords-jiang"], ["chords-chordino"], ["rhythm"], ["tonal"]]
}}}
```

`capture` and `backfill` are the only flow keys. `produce`, `attempt`, `features`
are the only selection fields. Omission inherits caller defaults; an explicit
empty array replaces that field with none. `produce` defaults empty in both.
Capture's other defaults are its present TARGETS and proposers.DEFAULT;
backfill's are its present UNITS and PROPOSERS. Preserve their order.

Legacy `backfill.units`, if supplied, replaces backfill feature defaults before
composition is applied. Supplying both `backfill.units` and
`composition.backfill.features` is an error; do not silently choose one.
An explicitly supplied `--proposers` replaces only `attempt`, including an empty
string. Its parser must distinguish omission from explicit input; it never sets
`produce`. Capture acquires no new command-line selection language in this work.
No environment-based selection or implicit file discovery is added.

For each result, methods are explicit proposer ids in order; all listed methods
run, including both chord methods when selected. Each must declare the exact
contract id/version; no inferred semantic version compatibility or display-name
matching. Unknown ids, duplicate methods, empty result method lists, unsupported
versions/scopes, non-file source references and unknown configuration fields
fail before work. Stable deduplication preserves first occurrence across fields;
a method in produce and attempt is executed once, with production enabled.
Proposer version and contract version remain different identities.

### Production and alternatives

Only `produce` expands missing proposer requirements. `features` requests exact
feature targets; `attempt` uses available inputs and creates no prerequisite.
An extractor preference alone never produces a stem (ADR 0076).

| Observed condition | Decision |
|---|---|
| Current resolver finds a usable cached path | Use that path, even if a higher alternative is missing; enqueue no speculative alternative |
| A preferred stem exists but its measurement does not | Preserve Bank.default_entry behavior; a cached mix is not substituted merely because it exists |
| No usable complete path; production explicitly requested | First declared production-capable path whose mandatory producers are configured; expand only that path |
| Preferred producer unconfigured | Consider next declared production path; if none, report unconfigured/blocked with causal prerequisite |
| Selected path held by earlier failure | Keep failed_before; do not evade it by choosing another producer |
| Selected path fails during execution | Report failure; no new automatic algorithm fallback |
| Valid measurement yields no observations | Report valid empty/no claim; no speculative producer fallback |
| Manifest's existing quality preference rejects a stem | Use existing Bank quality fallback; this is distinct from empty proposer output |
| Prerequisites complete or another worker fills cache | Re-resolve through Bank/runner before dispatch and proposal; eliminate satisfied work |
| Optional input absent | Continue existing default behavior; never create it solely for an optional read |
| Method lacks shared resolver or production recipe | Explicit unsupported-production diagnostic; opportunistic attempt remains available |

The first B milestone enables direct methods (chords, beats, key, tuning,
sections). This is staging, not final scope reduction. Before B/D final
acceptance, implement and enable these bounded recipes:

- Bass: produce stems, tonal on stems/bass, and mix rhythm. Resolve all stem
  measurements against the same selected stems entry, including equivalence.
- Bass-line: produce stems and tonal on stems/bass; prefer vocal-independent
  bass PESTO on stems/bass when configured, otherwise basic-pitch on stems/bass.
  A cached complete basic-pitch path needs no PESTO production. Optional mix
  tuning remains read-only. Selected-path held failure does not choose basic-pitch.
- Notes: use Bank.default_input for melody. With no preferred source, produce
  melody on mix and never make stems. With selected vocals, use existing vocal
  PESTO if available, otherwise use complete cached melody/notes on those
  vocals before producing anything. Only when neither cached path exists select
  configured PESTO on those vocals, or melody on that source when PESTO is
  unconfigured. Test cached vocal melody with absent PESTO explicitly. Existing manifest quality
  probes retain their ordering and may subsequently reject the vocal preference,
  causing Bank's existing mix fallback. Non-vocal preference uses melody on that
  source. Optional recording tuning, rhythm and bass-line bleed features are
  never produced solely for notes. This recipe does not select a cached mix
  while an existing preferred vocal input lacks its measurement.

These production choices are proposed opt-in behavior; existing opportunistic
attempt semantics do not change. Before each complex recipe is enabled, tests
must cover cold, cached lower alternative, unconfigured producer, held failure,
empty output, quality rejection, same-stem/equivalent candidate identity and
region dispatch. Unsupported-production diagnostics remain for incomplete
recipes during implementation, not as final completion of B/D. Timed lyrics
remains `not_yet`; this record supplies none of its missing prerequisites.

### Diagnostics, freshness and state

Extend existing dry-run/report output with selected result/method, reason,
source/region, requirements, alternative decisions, cache/equivalence/coverage,
effective key/parameters when known, dependencies, configured route, cost/source
or unknown, and causal blocking reason. Missing, unconfigured, failed_before,
damaged, unsupported, transport refusal and valid empty remain distinct.
Dry run may use existing read-only discovery subprocesses. It starts no
extractor or remote task, commits nothing through Door and does not mutate World.

Diagnostics include selection evidence revisions supplied by the caller, digest,
Catalog versions, effective config and any supplied policy identity. They are
operational explanations, not an executable plan/apply artifact (INV-19).

Existing dispatch checks remain authoritative: extract.Run.graph/run_one/make
resolve keys, reuse and validation; Run.verify_digest hashes content on first
actual extraction, not a fully cached run; run_default re-evaluates preferences.
remote.put verifies supplied digest, remote.work validates attachments and
reconstructs keys, remote.file_result validates outputs before placement. Only extractors with no
feature dependencies travel remotely. Catalog/configuration are frozen per run;
revalidation does not imply live configuration or route reload.
backfill.Planner.state and Planning.want reconcile bank/failure/route observations;
existing Runner controls dispatch. The implementation must verify these paths
again at dispatch, not assume an earlier explanatory report is a lock.

Before proposing, callers use their existing fresh export/ArchiveReader path;
propose.run obtains heads and propose_artifact pins stream and Artifact revisions.
A proposal records actual feature keys and outputs via proposers.read; Door's
existing admission validation governs the write. A bounded missing refresh or
identity check found by tests must be added explicitly. No transaction spans
archive reads, extractors and Door; a running job is not immediately cancelled
by curation edits. Existing follow/rescan timing remains.

Claims, parameters Artifacts and derivation runs are evidence, written through
Door. Feature entries remain regenerable. Expansion, dispatch bookkeeping and
diagnostics are operational and disposable. Composition JSON is source/operator
configuration, not admitted policy. No policy evaluator or syntax is added;
[open policy and capture recovery questions](../open-questions.md) remain open.

## Consequences and acceptance

Adding a direct method localizes its callable, declared inputs and resolver;
callers share selection expansion while retaining different intake/defaults.
Complex methods retain explicit specialized code instead of forcing their
semantics into a general dependency language. Existing extractor, proposer,
remote protocol and contract versions do not change for this refactor.

Gate A acceptance requires review of this record, the inventory and executable
characterization evidence. Later gates require registry-enumerating actual-read
tests, normalized payload/provenance compatibility, cold execution without an
archive and warm execution without processes, region/alternative/failure tests,
and unchanged capture/backfill defaults. A separate project, generic scheduler,
policy implementation and capture recovery are excluded. Rejection leaves the
existing architecture and runtime unchanged.
