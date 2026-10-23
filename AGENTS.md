# Working in this repository

Guidance for anyone changing this repository, agents and people alike;
`CLAUDE.md` imports it, and tool-specific additions go there. This repository
holds Archive's specification, architecture, and code. It is not an archive:
no personal archival data belongs here.

## Find what governs your change

Read [MAP.md](MAP.md) first: where each part lives, its tests, and the ADRs
and invariants that govern it. Then search instead of reading whole documents:

- the decision: `rg -n '<topic>' docs/decisions/README.md`, then that record;
- the rule: `rg -n 'INV-<n>' docs/invariants.md` (cite rules as `INV-2`);
- the word: `rg -n '<word>' docs/vocabulary.md`;
- what is open: `rg -n '<topic>' docs/open-questions.md`, so you do not
  answer an open question by accident.

Architecture is the vocabulary, the invariants, the state model, the
boundaries, the specification, and where a concept's code lives; it changes
only through a decision record. Check [docs/state-model.md](docs/state-model.md)
when code stores or caches anything, [docs/provenance.md](docs/provenance.md)
when it produces or ranks claims, [docs/boundaries.md](docs/boundaries.md)
when it adds a directory, dependency, or command, and the
[specification](spec/archive-spec.md) when it touches what ends up inside an
archive. [README.md](README.md) orients a newcomer.

## How to work

- **The invariants are the rules.** Every change keeps all of them.
- **Use the words as defined.** *Admit*, not "ingest"; *Door*, not
  "validator"; *plan / apply*, not "orchestrator". A new noun or meaning takes
  a decision record ([naming discipline](docs/vocabulary.md#naming-discipline)).
- **Classify what you store** as evidence, regenerable, or operational; code
  survives the loss of anything that is not evidence.
- **Propose; let Door commit.** Nothing outside Door changes archival state.
- **Carry provenance:** who, how, from what, under which policy.
- **Desired state in policy, mechanism in code.**
- **Inherit before inventing:** PROV, Web Annotation, SKOS, the library
  reference model ([ADR 0012](docs/decisions/0012-inherited-meaning.md)).
- **Keep history quiet.** A rerun on unchanged inputs writes nothing.
- **Translate when porting from V2** ([ADR 0015](docs/decisions/0015-porting-from-v2.md)):
  this vocabulary wins, and V2 never answers an open question. Find a V2
  unit's row in [docs/port-ledger.md](docs/port-ledger.md) first; update it
  when the unit lands.
- **Place code by concept.** If no concept fits, raise the question.
- **Build the boring thing.** What is
  [left out on purpose](docs/boundaries.md#left-out-on-purpose) stays out
  without a decision record; unimplemented behaviour fails loudly.
- **An open question is a boundary, not a TODO.** Keep mechanism below it and
  the behaviour unsupported; never settle it in code.
- **Governance grows with irreversibility:** derivation runs unattended,
  publication needs approval, destruction needs a person, and disclosure is
  recorded before it happens.
- **Write for someone who has lost the context:** reasons, invariants, and
  what was left out. Decisions and the specification last; plans do not.

## When an invariant is in the way

**Stop**; do not work around it or hide an exception in code. Write down what
you were building, which invariant or decision blocks it, and why; propose a
decision record with status `proposed`, and leave the invariant until a
person accepts the change.

## Checks

`make check` (suite `check`): links, the specification's version, the ADR
index, cited invariants, orphans, licences in `REUSE.toml`, and the old
umbrella project's word. `make test` builds Door and Archive and runs every
suite. With wf: `wf check --tier 30 --changed` before a commit (`check`,
`docs` for stale claims in the docs, and the fast suites your change
touches), `wf check --tier night --changed --on laptop` before a merge.

## Pruning

Use the `prune` skill. Here: the V2 port ledger accounts for units until the
port is complete, and there is no usage history yet, so absence is no
evidence. Never delete a specification version or contract an archive may
cite; supersede it. Meanings live in the vocabulary, rules in the invariants,
reasons in decision records, gaps in the open questions. The specification
restates the invariants and the README repeats the vocabulary's concept table
on purpose (`make check` keeps them equal). `make claims` lists sentences
about the present for a person to recheck.
