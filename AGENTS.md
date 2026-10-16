# Working in this repository

This is the guidance for anyone changing this repository, coding agents and
people alike. `CLAUDE.md` imports it; tool-specific additions go there, never a
second copy.

This repository holds Archive's specification, architecture, and eventually its
code. It is not an archive: no personal archival data belongs here.

## Before changing anything architectural

Architecture means the vocabulary, the invariants, the state model, the
boundaries, the specification, or where a concept's code lives. Before changing
any of it, read:

1. [README.md](README.md), for orientation;
2. [docs/vocabulary.md](docs/vocabulary.md), for what the words mean;
3. [docs/invariants.md](docs/invariants.md), for what must hold (cite them as
   `INV-2`);
4. the [decision records](docs/decisions/README.md) that touch your change;
5. [docs/open-questions.md](docs/open-questions.md), to check you are not about
   to answer one by accident.

Also read [docs/state-model.md](docs/state-model.md) when code stores or caches
anything, [docs/provenance.md](docs/provenance.md) when it produces or ranks
claims, [docs/boundaries.md](docs/boundaries.md) when it adds a directory,
dependency, or command, and the [specification](spec/archive-spec.md) when it
touches anything that ends up inside an archive.

## How to work

- **The invariants are the rules.** Every change must keep all of them. This
  file does not repeat them.
- **Use the words as defined.** No synonyms: say *admit*, not "ingest"; *Door*,
  not "validator"; *plan / apply*, not "orchestrator". Changing what a word
  means, or adding a new noun, takes a decision record
  ([naming discipline](docs/vocabulary.md#naming-discipline)).
- **Classify everything you store** as evidence, regenerable, or operational.
  Code must survive the loss of anything that is not evidence.
- **Propose; let Door commit.** Code outside Door never changes archival state
  directly.
- **Carry provenance.** Every claim and consequential change says who, how, from
  what, and under which policy.
- **Put desired state in policy and mechanism in code.** Before adding a special
  case to code, ask whether it is a desired state. Before adding a feature to the
  policy language, ask whether it is mechanism.
- **Inherit before inventing.** Use the adopted vocabularies (PROV, Web
  Annotation, SKOS, the library reference model) before creating a term
  ([ADR 0012](docs/decisions/0012-inherited-meaning.md)).
- **Keep history quiet.** Compare before writing; a rerun on unchanged inputs
  writes nothing.
- **Translate when porting from V2.** Rename to this vocabulary; where V2 and
  this repository disagree, this repository wins, and V2 never answers an open
  question
  ([ADR 0015](docs/decisions/0015-porting-from-v2.md)).
- **Use the port ledger during the V2 port.** Before touching a V2 unit, find
  its disposition and boundary in [docs/port-ledger.md](docs/port-ledger.md).
  Update the row when the unit lands; the ledger is deleted when the port is
  complete.
- **Place code by concept.** If no concept fits, raise the question instead of
  inventing a subsystem to hold the code.
- **Build the boring thing.** Small tools, and the minimum a settled contract
  needs. What is [left out on purpose](docs/boundaries.md#left-out-on-purpose)
  stays out without a decision record. Unimplemented behaviour fails loudly; it
  never pretends to succeed.
- **An open question is a boundary, not a TODO.** When the documents leave
  something open, say so, or add it to the open questions. Stop at the boundary:
  keep reusable mechanism below it and leave the behaviour unsupported. Never
  settle an open question in code.
- **Governance grows with irreversibility.** The harder an act is to undo, the
  more it needs: derivation runs unattended, publication needs approval,
  destruction needs a person, and disclosure is recorded before it happens. Use
  this to judge a case the documents do not name.
- **Write for someone who has lost the context.** Record why: reasons,
  invariants, and what was left out on purpose. Plans are disposable; decisions
  and the specification last.

## When an invariant is in the way

If an implementation would break an invariant or an accepted decision, **stop**.
Do not work around it, weaken it locally, or hide an exception in code.

Write down the contradiction: what you were building, which invariant or
decision blocks it, and why. Propose a new decision record with status
`proposed`, and leave the invariant as it is until a person accepts the change.

## Checks

`make check` validates the repository's structure: links resolve, the
specification's version is consistent, every decision record is indexed, cited
invariants exist, every document is linked from somewhere, every file has a licence in
`REUSE.toml`, and the word for the
old umbrella project never appears. Run it after any documentation change.
`make test` builds the Haskell code and exercises Door and Archive through
their command interfaces. Run both before committing code.

## Pruning

The `prune` skill supplies the method; this names what is specific here.

- **Ledgers:** the temporary V2 port ledger accounts for reference units until
  the port is complete. There is not yet production usage history, so absence
  of evidence is not enough to prune a working mechanism.
- **Never delete:** a specification version or contract that an archive may
  cite. Both are copied into every archive, so they outlive this source tree;
  supersede them instead.
- **Canonical homes:** meanings in the vocabulary, rules in the invariants,
  reasons in decision records, gaps in the open questions, and what an archive
  must satisfy in the specification. Deliberate duplication: the specification
  restates the invariants because it must stand alone inside each archive, and
  the README repeats the vocabulary's concept table (`make check` keeps them
  equal).
- **Checks:** `make check`; `make claims` lists sentences about the present
  ("will be", "not yet", "currently") for a person to recheck.
