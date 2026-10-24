# 0117. CI builds the Haskell package once, caches it, and runs the suites side by side

Status: accepted (operator, 2026-10-24); amends
[ADR 0062](0062-release-bundle-and-ci.md)'s "CI runs everything on every push"
and [ADR 0115](0115-ci-skips-prose-only-pushes.md)'s cancelling of superseded runs

## Context

CI's one job ran `nix flake check`: the Haskell build with `cabal test all`,
then every Python suite one after another, then the image. A run took 26 to
38 minutes, about 35 of them in that one step. Three things made it slow:

- Nothing was cached between runs. The Haskell package's source was the
  whole repository, so a change to any file, Python or prose, rebuilt and
  retested it.
- The suites ran in series although none needs another's result; the enrich
  suite alone takes about 20 minutes.
- A pull request's branch ran twice, once for the push and once for the pull
  request, and the cancel-superseded rule did not join the two.

## Decision

**The Haskell package reads only its own files.** `flake.nix` builds it from
`archive.cabal`, `cabal.project`, `door/`, `archive/`, `spec/` (its data
files, which its tests also read), and `fixture/plan.json`, which a Door test
checks. A change elsewhere leaves its derivation
unchanged.

**Three Python checks instead of one.** `checks.python` runs World with its
conformance tests, relay, `enrich/tests`, shadow, migration, and fixture;
`checks.enrich` and `checks.capture` run those suites on their own
interpreter. `nix flake check` still runs all of them, and `release.yml`
still runs it before publishing.

**CI builds the package once and runs the rest side by side.** The `archive`
job builds `checks.archive` and saves its Nix store with
`nix-community/cache-nix-action`, keyed by a hash of what that build reads
and the flake. The store is collected down to 1 GB first, so the cache holds
the package's closure and not the compiler, which cache.nixos.org serves;
older caches of the same key prefix on the same branch are purged. The
`tests` jobs (python, enrich, capture) and the `image` job need `archive`,
restore its cache, and run in parallel. A change that leaves the Haskell
sources alone restores the package rather than building and testing it again.

**A change runs once.** CI runs on a push to `main` and on pull requests, not
on pushes to other branches. Runs are grouped by the pull request's branch, so
a newer push cancels the older run; `main` keeps every run.

## Consequences

- A branch without a pull request gets no CI run; open one, even a draft.
- A test the Haskell suites run that starts reading a file outside those
  paths must add it to `haskellSource` and to the cache key in `ci.yml`, or a
  change to that file would reuse a stale build.
- The cache relies on the cache.nixos.org substitutes for everything it
  drops; an outage there slows a run, but does not change what it checks.

## Alternatives considered

- **Magic Nix Cache.** GitHub retired the cache API it used.
- **A binary cache service such as Cachix.** It needs an account and a secret;
  the GitHub cache needs neither and fits the repository's 10 GB limit.
- **Caching every job's store.** Each would hold its own copy of the same
  closure, with little more gained: the Python environments come from
  cache.nixos.org or pinned wheels in seconds.
