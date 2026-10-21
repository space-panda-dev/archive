# 0115. CI skips the build for a push that changes only prose no build reads, and cancels superseded runs

Status: accepted (operator, 2026-10-21); amends
[ADR 0062](0062-release-bundle-and-ci.md)'s "CI runs everything on every push"

## Context

ADR 0062 runs every check on every push. The build and test job takes most
of an hour, and a push that changes only an agent-facing document or a
decision record runs it in full although nothing it builds or tests reads
those files. A second push to a branch while the first still runs also
runs both to the end, though only the newer result is wanted.

## Decision

**The structure checks run on every push.** `make check` moves to its own
workflow, `.github/workflows/structure.yml`, with the job name `structure`
unchanged. Documentation is what it checks, so it never skips.

**The build and test workflow skips prose that no build reads.**
`.github/workflows/ci.yml` ignores a push or pull request whose every changed
path is a top-level `*.md` file, a decision record under `docs/decisions/`,
or `.workshop.toml`. Nothing in the Haskell package, the Python suites, the
image or the bundle reads them. Any other change, including `docs/` files
that tests read (the feature-bank document, World's README) and the
specification's Markdown, runs everything as before. A file added to that
list later must be one no build or test reads.

**A newer run replaces an older one on the same branch.** Both workflows
cancel a run still in progress for the same branch or pull request when a
newer one starts, except on `main`, where every commit keeps its run.

Releases are unchanged: `release.yml` repeats every check before it
publishes.

## Consequences

- A required status check named after the old `CI` workflow's `structure`
  job may need to be renamed in the repository's settings.
- A test that starts reading one of the ignored files must take it off the
  list, or a change to it would skip the test that reads it.

## Alternatives considered

- **Ignore every Markdown file.** Tests read some (`docs/feature-bank.md`,
  `world/README.md`), and the specification is Markdown.
- **A path filter inside the job.** It needs a third-party action, for the
  same effect.
