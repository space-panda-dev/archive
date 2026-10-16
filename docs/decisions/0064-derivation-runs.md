# 0064. Derivation runs are claims, and enrichment code lives in `enrich/`

Status: accepted

ADR 0006 requires every kept enrichment result to say how it was made, and
[provenance](../provenance.md) asks each claim for "the model or method, its
version, and its configuration". Two installed contracts already point at a
run: `work_match` version 1 requires one `run` reference and
`capture_alignment` version 1 allows one. Neither names what a run is, so the
specification forbids admitting a match (D-14), and the open question "what
run record contract lets a `work_match` claim be admitted?" has blocked every
machine claim since 0.0.5. ADR 0047 also called the migration's sample-rate
probe "a recorded derivation run", but no such record existed, so the
migration wrote the probe into each translation's payload instead.

The operator asked on 2026-10-16 for machine-suggested spans, audio facts,
and matches to reach World, which needs runs first.

## Decision

**A run is a Claim under `archive.claim/derivation_run` version 1.** It
describes one execution of one method as a PROV Activity:

- payload `method`: `id` (1 to 200 characters) and `version` (1 to 100);
- payload `software`: zero to 32 `{name, version}` pairs naming the programs
  and libraries whose behaviour the output depends on (a decoder, an analysis
  library), in the order the tool reports them;
- payload `started_at` and `ended_at`: RFC 3339 instants in UTC with a `Z`
  suffix, where `ended_at` is not before `started_at`;
- references `used` (zero or more, each pinning an exact Artifact, Claim, or
  Entity revision the run read; mapped to `prov:used`) and `parameters` (at
  most one Artifact revision holding the run's configuration as canonical
  JSON).

The envelope `agent` is the tool's Package URL. Its identity name is
`run-` followed by a lowercase RFC 9562 UUIDv4, in namespace
`archive.derivation/run`; every run is a new object.

**Outputs point at their run.** A claim made by a run pins it in role `run`,
mapped to `prov:wasGeneratedBy`. The run and its outputs are proposed in one
admission batch, so a run never exists without what it made and an output
never exists without its run. A run that keeps no output is not proposed.

**Configuration is an input, not a field.** As ADR 0006 requires, presets,
thresholds, and prompts go into the `parameters` Artifact, never into the run
or output payloads. Identical parameters have identical bytes, so a tool may
reuse the Artifact that already holds them.

**Enrichment code lives in `enrich/`.** Each tool takes files and writes a
plain result; a small adapter in `enrich/` turns a result into an admission
proposal with its run. Tools never read or write an archive directly
(ADR 0006, [boundaries](../boundaries.md)).

## Consequences

- `work_match` and `capture_alignment` get version 2, whose `run` target pins
  this contract (ADR 0067).
- Door gains one profile rule: two named instant fields where the second is
  not earlier than the first.
- The migration's inline probe stays as admitted: those translation records
  are already in archives and are migration provenance, not enrichment.
  ADR 0047's "recorded derivation run" is read as that inline record.
- World projects a run as a `prov:Activity` with its method, software,
  times, inputs, and parameters.

## Alternatives considered

- **Run as an Entity.** Entities carry only `kind` and `label`, so the
  method, times, and inputs would have nowhere to go.
- **Method and tool versions in each output's payload.** It repeats the same
  facts on every output and cannot say which outputs came from one execution.
- **Software as Entities.** Closer to PROV, but every library version would
  need its own object before the first run; a later version can add
  references without changing this one's meaning.
