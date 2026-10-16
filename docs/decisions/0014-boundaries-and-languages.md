# 0014. Spool, languages, and repository boundaries

Status: accepted

The archive depends on work distribution, on a strongly typed core, and on tools
written in whatever suits them. Those choices need fixed edges so they do not
leak into each other.

## Decision

**Spool is independent.** Spool distributes durable work
(a task is put, leased by a worker, then acknowledged or failed) and lives in its own repository.
Archive may use it; it never learns what an artifact, an enrichment, or World
is. Archive meaning travels in payloads Spool treats as opaque. Work done
through Spool gains no archival standing until its result is proposed to Door.

**Door is Haskell.** Door is where invalid archival states must be impossible to
write, so its types make them unrepresentable. The archive's reading tools share
Door's types and are Haskell too.

**Everything else chooses its language against its contract.** The policy
planner is Python using clingo. Enrichment tools may be written in anything
that accepts their declared input and produces their declared output. The
repository is polyglot; its durable artifacts are prose, the specification, and
policy.

## Rejected

- **Spool inside Archive.** A random unrelated program should be able to use
  Spool.
- **One language for the whole repository.** Only Door needs Haskell's
  guarantees.
- **The unmaintained Haskell bindings to clingo.** The last release was in 2017
  and does not build.
