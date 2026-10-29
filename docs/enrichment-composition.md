# Composing enrichment

Use this guide when adding a proposer or selecting existing methods. For an
extractor, start with [adding an extractor](adding-an-extractor.md).
[ADR 0120](decisions/0120-internal-enrichment-composition.md) owns the architecture;
[the feature bank format](feature-bank.md) owns bytes and keys. Examples here are
synthetic and do not describe an archive or a deployment.

## Select work

The existing extractor JSON configuration accepts `composition.capture` and
`composition.backfill`, selected by capture `--extract-config` and backfill
`--config` respectively. Each flow has three fields:

| Field | Selection |
| --- | --- |
| `produce` | Exact contract id/version and ordered proposer methods; expands missing mandatory inputs |
| `attempt` | Methods to try on available inputs; creates no prerequisites |
| `features` | Extractor/source targets independent of claim production |

A synthetic feature-only selection is:

```json
{"composition": {"backfill": {
  "produce": [], "attempt": [], "features": [["tonal"]]
}}}
```

A result uses `{"contract": ["archive.claim/key_regions", "1"],
"methods": ["key"]}` inside `produce`. Check exact contract versions in
`proposers.PROPOSERS` when adapting examples. All listed methods run; these are
not algorithm fallbacks. Feature targets are `["extractor"]` for mix or
`["extractor", "stems/bass"]` for a file-feature source.

Omission inherits caller defaults; `[]` replaces a field with none. Production
defaults empty. Legacy `backfill.units` replaces feature defaults; supplying it
with `composition.backfill.features` is an error. An explicit `--proposers`
replaces only `attempt`, including an empty string; omission retains configured
defaults. Stable deduplication preserves first occurrence; a method in both
production and attempt runs once with production enabled. Unknown fields, ids,
contract versions, unsupported scopes, invalid sources and duplicate methods
within a result fail validation before execution.

Extractor preferences alone never request stems. A complete cached alternative
wins before speculative production. An existing preferred stem with a missing
measurement retains Bank's preference rules rather than selecting a cached mix.
When production is requested and no cached complete path exists, the first
configured recipe is selected. Missing or unknown capability blocks missing
work. Optional reads never produce work. Held failures and valid empty results
do not authorize another producer. Manifest quality rejection retains Bank's
existing fallback behavior.

## Extend the flow

1. **Add an extractor:** follow the extractor guide through manifest, runner,
   version/digest pins and shipped-table updates. Completion means synthetic
   cold output validates and a warm run passes with process launch replaced by
   a failure. Manifest discovery does not select a proposer or change defaults.
2. **Add a proposer:** register its callable and `Requirement` together in
   `enrich/proposers.py`. Declare exact contracts, supported `whole`/`region`
   scopes, mandatory `FeatureRead` outputs for each `InputPath`, ordered
   `FeatureTarget` production recipes and conditional optional reads.
   `production=None` makes a path attempt-only. Proposer and contract versions
   are separate identities.
3. **Share resolution:** both execution and expansion use
   `resolve_inputs(proposer_id, context)`. Direct methods use the direct
   resolver; specialized choices use bounded named resolvers over declared
   inputs. Preserve Bank entry identity, equivalent stems and region placement.
   Record actual consumed outputs through `proposers.read`, rather than citing
   every optional possibility. Completion requires registry-enumerating tests
   covering actual reads, missing and malformed inputs, valid empty output,
   scopes and regions. Specialized recipes also cover cached lower alternatives,
   unconfigured producers, held failure and quality rejection.
4. **Compose existing methods:** select their exact contracts and methods in
   configuration. Keep capture stages and Runner unchanged. Callers translate
   expanded targets into existing extraction/backfill mechanisms. Prove a
   synthetic request reaches the real caller, extraction, proposal and Door;
   compare normalized payloads/provenance and rerun unchanged inputs to assert
   no duplicate derivation or evidence writes. Bump extractor versions when
   output bits can change and proposer versions when claim semantics change.

The notes recipe follows Bank.default_input: no preferred source means mix
melody without making stems; cached vocal melody can satisfy the path without
producing PESTO. Optional tuning, rhythm and bleed inputs remain read-only.
Timed lyrics remains unsupported; composition supplies no missing prerequisites.

### Configured vocal-note production

`vocal-notes` produces the same notes contract through the shared notes reading,
but requires `vocal-contour`; a cached legacy melody does not satisfy it. Select
it explicitly in `produce`, with the existing provisioned model parameters and
programs. Remove the generic `notes` method from `attempt` when replacing that
method: otherwise both named methods remain requested, as composition normally
specifies. For a notes-only production selection:

```json
{"composition": {"backfill": {
  "produce": [{"contract": ["archive.claim/notes", "1"], "methods": ["vocal-notes"]}],
  "attempt": [], "features": []
}}}
```

This does not change unconfigured installations or the ordinary notes method.
A complete cached contour keeps its exact stem entry. Missing measurements use
an existing preferred four- or six-stem provider before requesting separation.
The contour, provisional note reading and zero-adjustment A440 policy remain
separate; charts, lyrics, keys and beat grids are not inference inputs.

`enrichment_requests.request` reads that same frozen backfill configuration and
uses `expand` plus ordinary idempotent Bank requests. A requested display region
is retained separately from the inference region: an existing whole-recording
stem selects whole-recording inference, rather than cutting or separating it
again. The existing backfill publishes and refreshes World per requested
recording, including when a feature was already cached. It does not expand that
request into a catalog-wide proposal round. Readiness requires a current notes
revision with the exact normal derivation identity and payload; ambiguous or
unindexed alternatives never become a guessed preferred revision.

## Read explanations

`selection(config, flow, defaults, proposers_override)` merges selections.
`expand(selection, context, catalog, parameters, capabilities)` returns targets,
proposer ids and structured diagnostics. Catalog owns dependency and parameter
validation; Bank owns keys, input choice and equivalence. Composition starts no
extractor, schedules no resource and admits no evidence. Operator configuration
is not admitted policy.

Diagnostics explain explicit features, cached paths, opportunistic attempts,
unconfigured paths, blocked production and deferred input choices. Re-resolve
after prerequisites: an explanation is not an executable plan or dispatch lock.
Existing runner dispatch and fresh proposal checks remain authoritative.
Missing, damaged, held failure, transport refusal and valid empty outcomes
retain their existing distinctions. Composition reports unknown cost as `null`;
backfill owns scheduling estimates and their measurement sources.

Capture adds `composition_explanation` beside its existing `composition`
diagnostics. Backfill's existing `composition` event adds `explanations`, indexed
by recording with separate whole and region explanations. Each envelope includes
the content digest, explicit region, caller evidence pins and snapshot when
available, frozen configuration identity, Catalog versions, effective parameters,
selected methods and exact result contracts. Inputs carry actual Bank-selected
keys, source placement, outputs and coverage; missing sources retain unknown
keys. An `expected_key`, when present, comes from Catalog for a known mix input
and does not assert a cached entry. Optional declarations remain unread.

Target route and cost fields carry caller observations with their source, or
unknown values. Backfill also reports its existing planner's
`dispatch_observation`, preserving causes such as `failed_before`, `damaged` and
`unconfigured`. Its selection candidate pins explain the discovery snapshot;
they do not assert that every candidate contributed a selected span. Repeated
expansion replaces the explanation for the same scope while preserving other
regions. These private operational reports contain no executable apply token.

For proposals, whole entries are tried first, then `RegionView` provides region
fallback where whole reads lack features. Preserve stream bounds and placement.
The relay separately serves region file features through `Practice.regional`
and keyed lookup; this does not make every relay read region-aware.
