# 0127. CI uses native parallel runners and complete test shards

Status: proposed; amends [ADR 0117](0117-ci-builds-haskell-once-and-runs-suites-side-by-side.md)

## Context

The operator asked for routine CI to finish within five minutes without
removing validation. The [measured pre-change run](https://github.com/space-panda-dev/archive/actions/runs/41134149734)
took 19 minutes 43 seconds. Enrichment and the combined Python check were
independent long paths. Downstream jobs also fetched interpreters after
restoring a cache that rooted only the Haskell package.

A custom process scheduler demonstrated that parallel execution helps, but
owning a new test runner and result protocol is unnecessary if an established
runner preserves these tests. `unittest-parallel` uses native unittest suites,
including class fixtures, subtests, and module cleanup. The Haskell package
continues to use Cabal's existing test runner.

## Decision

**Execution belongs to maintained runners.** Nix pins the public
`unittest-parallel` wheel and its dependency by hash, in test-only Python
environments. Production interpreters do not gain these dependencies. Whole
modules are the default grouping; enrichment uses classes after checking that
it has no module setup/teardown fixtures and that its cached synthetic audio
survives fresh processes per class. Scientific libraries use one thread per
worker, and the worker count is explicit.

**Existing suite boundaries run independently.** World, relay, enrichment's
integration tests, shadow, migration, fixture, documents, and capture have
separate Nix checks. The aggregate checks remain available to existing callers.
The release still builds every check and validates the image and bundle.

**Enrichment shards select tests; they do not execute them.** A small native
unittest `load_tests` adapter discovers the original corpus, rejects discovery
errors and duplicate IDs, and partitions sorted module names by shard index.
Modules stay intact during selection, and new modules are assigned
automatically. Contract tests check exact, disjoint coverage, deterministic
assignment, and invalid shard bounds. The maintained runner owns process
scheduling, fixture execution, and result handling. Tests which themselves
create process pools run under standard unittest in a separate serial phase,
because unittest-parallel workers are daemon processes. Optional real-model
classes also stay exclusive in that phase. The two phases partition the full
inventory; neither skips tests to make parallel execution pass.

**One declaration drives Nix and the hosted matrix.** `lib.ciChecks` includes
every ordinary suite and every generated enrichment shard. CI reads that list
rather than maintaining another list in YAML. A failed job fails CI; workers
are not retried to turn failures green. Structure checks continue on all
changes under ADR 0115.

**Cache runtime dependencies rather than weakening checks.** The prerequisite
job roots the tested Haskell package, test interpreters, and native runtime
tools through `ci-dependencies`. Downstream jobs restore that closure and still
ask Nix to build their exact derivations. A fallback cache is an optimization,
not evidence that a changed test passed. Old branch caches are purged and
build-only dependencies can be substituted again.

## Consequences

The five-minute goal concerns measured complete routine runs, including setup,
cache restoration, image construction, and smoke testing. A cold compiler
build or hosted queue delay can exceed that budget. Record actual hosted
measurements before claiming the goal is met.

A future module-level fixture in enrichment must be kept as one scheduling
unit or run with module grouping. Optional real-model tests require exclusive
execution when their external tools are enabled; parallel CI must not silently
replace an environment that exercises those tools with one that skips them.

## Alternatives considered

- **Publish the experimental custom runner.** It would add a public project,
  protocol, and compatibility surface to maintain. Native runners should be
  measured first.
- **Run more processes on one hosted VM.** That does not add CPU capacity and
  increases memory contention. Independent hosted shards supply the capacity.
- **Drop slow tests or use retries.** Neither preserves the validation requested
  by the operator.
