# Archive

**Archive preserves durable evidence across decades.**

This repository holds Archive's architecture, its specification, and eventually
its software. It does not hold an archive. The actual archive, the data, lives
elsewhere and is governed by the specification kept here.

> **Status.** The Haskell port is in progress. `archive init` creates the
> accepted Git/git-annex layout and bootstrap records, and
> `door admit --proposal <directory> --dry-run` validates retained proposal
> metadata against one committed snapshot and predicts RFC 8785 revision
> identities without transferring content. Persistent admission commits each
> batch once. For content, Door obtains each declared key from its configured
> staging remote with git-annex, has git-annex hash the local object, compares
> SHA-256 and size with the proposal, and commits the records and content
> locations together; exact retries return before git-annex runs.
> Transition proposals validate exact
> retire/destroy contracts, authorization, expected heads, and durable
> version-2 batch order. Persistent transition writes the complete batch in one
> Git commit without invoking git-annex; exact retries preserve the first
> timestamp and create no commit. Both modes replay committed history
> from its proven root, recover durable transition batches, and refuse changed
> or incomplete normative history. `archive status`,
> `doctor`, `ls`, and `show` read one pinned committed tree and report incomplete
> evidence explicitly; `current` remains unsupported at its named open question.
> `archive maintenance prune-packed` coordinates loose-object pruning under the
> archive lock after verifying and synchronizing existing packs and ref-reachable
> objects; it proves refs unchanged and synchronizes the result.
> Admission and retire/destroy transitions are implemented,
> but their persistent success responses do not yet meet the accepted
> power-loss durability contract ([ADR 0031](docs/decisions/0031-power-loss-durability.md)).
> Do not treat those responses as evidence that a commit survived power loss.
> The V2 spike remains reference
> material and is being translated into this repository's vocabulary
> ([ADR 0015](docs/decisions/0015-porting-from-v2.md)).
> An [experimental World projection](world/README.md) can rebuild a disposable
> RDF store from a complete committed Archive export and answer local SPARQL
> queries ([ADR 0041](docs/decisions/0041-world-v1-projection.md)). Work and
> performance relations are defined in [ADR 0042](docs/decisions/0042-work-correspondence.md),
> and exact audio selections and song occurrences in
> [ADR 0045](docs/decisions/0045-audio-claim-contracts.md). Their contracts
> are installed in specification 0.0.4. Specification 0.0.5 adds the review
> application contracts of [ADR 0048](docs/decisions/0048-review-app-write-contracts.md)
> (multi-source selections, drift-aware alignment, selection facts,
> judgments, sessions) and the V2 migration route contracts of ADR 0049.
> Door admits all of them; publication approval and withdrawal are committed
> only through `door transition`, authorized by the active operator
> ([ADR 0051](docs/decisions/0051-publication-transition-dispatch.md)).
> Specification 0.0.7 adds derivation runs, audio stream facts, span
> suggestions, and matches and alignments that pin their run
> ([ADRs 0064](docs/decisions/0064-derivation-runs.md) to
> [0067](docs/decisions/0067-matches-and-alignments-pin-runs.md); the bundles
> as proposed are in [0064-contracts](docs/proposals/0064-contracts/)), which
> Door admits and World projects, and the specification upgrade contract of
> [ADR 0068](docs/decisions/0068-specification-upgrade.md), by which
> `door transition` moves a 0.0.6 archive to 0.0.7 in one commit.
> Per-kind current rules remain open.
> Except where a command is explicitly implemented, the material below
> describes commitments rather than running software.

## Executable configuration

Before running Archive or Door commands that use Git, set
`ARCHIVE_GIT_EXECUTABLE` to the absolute path of the Git executable you trust.
Set `ARCHIVE_GIT_ANNEX_EXECUTABLE` to the absolute path of git-annex for
`archive init`. The operator may maintain either installation. Archive resolves
configured symlinks and refuses missing or relative paths; it does not select
these programs from `PATH` at operation time ([ADR 0033](docs/decisions/0033-operator-configured-executables.md)).

## What Archive is

Archive keeps **evidence**: original files, the things and claims it records,
judgments about those claims, machine results worth keeping, the policy it runs
under, and the history of consequential changes. It owns that evidence. It is
not blob storage with the real knowledge kept somewhere else.

It does not keep everything it computes. Caches, indexes, views, and the answer
to "which claim is current?" are **regenerable**: losing them costs time, not
history.

It is curated, not append-only. Things can be taken out of the collection, and
their bytes can be destroyed. The rule that holds everything together is:

> An admitted object is never silently changed, and archival state changes only
> through Door.

Archive stands alone. It works with sibling projects through protocols and
programs, never through a shared framework.

Archive takes a side. The means of preservation (the code and the contracts)
are a commons anyone may use and no one may enclose. The content is its
owner's, and nothing leaves without a grant and a record. The people an archive
records can limit where it goes. Archives made with Archive belong to the
people who keep them, not to Archive.

One design principle runs through all of it: **what the archive should hold
belongs in policy; how it gets there belongs in code.** The policy language is
kept deliberately less powerful than the code.

## The concepts

| Concept | Verb | Responsibility |
|---|---|---|
| Archive | preserve | Durable evidence |
| Door | admit / transition | Sole authority for archival meaning |
| World | relate / query | Assemble evidence into a broader referential universe |
| Enrichment | derive | Machine/computational derivation without source mutation |
| Annotation | assert | Human-authored claims |
| Review | assess / adjudicate | Judge claims and settle between competing ones |
| Policy | declare | Human-authorized desired archival state |
| Plan / apply | reconcile | Compute the difference from policy, then act on it |
| Spool | distribute | External domain-agnostic work protocol |

These are meanings, not services. The diagram shows who may change what; it
says nothing about processes or machines.

```
 Policy ──► plan ──► apply ──┐
 Annotation ─┐               │
 Review ─────┤               ▼
 Enrichment ─┼── propose ──►  Door  ── commit ──►  archive (records + content)
 World ──────┘                                        │
     ▲                                                │
     └────────────── read / project ◄─────────────────┘
 Spool: an external work protocol. Archive uses it; it knows nothing of Archive.
```

## What is settled

Every point below has a [decision record](docs/decisions/README.md), and most
have an [invariant](docs/invariants.md).

**Door.** Nothing becomes "the archive says X" until Door admits it or records
a transition. Door decides whether a change is valid, never whether it is
wanted. It does not move bytes: git-annex does that, under intent Door has
sanctioned. There is one Door, on one machine; other machines stage content and
propose it. Door is written in Haskell so that invalid states cannot be written
down.

**Records.** Evidence is stored as immutable revisions in four shapes:
artifact, entity, claim, and assessment. Records live in Git; bytes live in
git-annex. An object, its bytes, and where those bytes sit have separate
identities, so moving a file never changes a record. Every record names the
program that wrote it and the agent (person, model, or process) that asserted
it.

**Claims and judgment.** People annotate; machines derive; Review judges.
Nothing that is judged is erased, and disagreement is kept. How much a claim is
believed depends on its provenance, not on whether a person or a machine made
it. Who may *cause* something is a separate question, and for consequential
acts the answer is the operator.

**Deletion.** Retiring takes an object out of the collection and keeps its
record. Destroying removes its bytes from every copy the archive controls and
reports any copies it cannot reach. Both are always a person's decision.

**Copies.** Placement is counted in failure domains (one disk, one house, one
provider), not in repositories. Only copies the archive controls count, plus
outside copies that were recently verified. Symlinks and published copies never
count. A source such as a recorder card is cleared only after the archive says
it is safe, and that verdict is kept.

**Policy.** Policy declares what the archive should hold: where copies live,
which enrichments should exist, what needs review, what to publish. It can also
recommend things to retire or destroy, but only a person acts on those. Rules
only add; no rule overrides another; a missing fact never causes anything to be
reduced. Anyone may propose policy; the operator authorizes it. It is written in
a small Datalog subset defined by the specification, so it outlives any one
engine.

**Plan, then apply.** A plan lists what should change and why. Apply carries it
out, unattended for routine work, and re-checks everything first. History stays
quiet: running either on an unchanged archive writes nothing.

**Publication.** Every destination has an exposure class (private, circle,
service, open, or distribution) that policy declares. An approval covers one destination and
exact versions. The operator's own work publishes automatically once approved;
anyone else's work needs a fresh approval before it goes beyond the operator's
circle, and unknown rights never widen exposure.

**Disclosure.** Sending content anywhere outside the operator's custody,
including to a hosted model or someone else's machine, is disclosure. It is
denied unless policy grants it, and every disclosure is recorded.

**Things as well as bytes.** Physical collections (records, books, notebooks,
tapes) are kept as collection records: accessioned and deaccessioned like
library holdings, described by claims, and digitised as derived artifacts.

**World.** World relates the archive to the outside universe and answers
questions over it. It is a projection: rebuildable, never the owner of what the
archive knows. Anything World learns that is worth keeping comes into the
archive as a claim.

**Inherited meaning.** Records map their fields to established vocabularies
(PROV, Web Annotation, SKOS, the library reference model, MusicBrainz), and
PREMIS and OAIS are mapped rather than adopted. Archive keeps its own contracts
where they are stricter.

**Operator and siblings.** An archive has at most one active operator; handing
it to someone else is recorded. Spool is a separate project in its own
repository.

## What is provisional

- **The specification** is a small pre-1.0 contract at version 0.0.7
  ([current spec](spec/archive-spec.md), [0.0.7 release copy](spec/0.0.7/archive-spec.md), [0.0.6 release copy](spec/0.0.6/archive-spec.md), [0.0.5 release copy](spec/0.0.5/archive-spec.md), [0.0.4 release copy](spec/0.0.4/archive-spec.md), [0.0.3 release copy](spec/0.0.3/archive-spec.md)); unresolved subjects remain explicitly outside
  its normative protocols.
- The unresolved V2 audio migration gates are recorded in the
  [release readiness note](docs/proposals/v2-migration-release-readiness.md).
  [ADR 0046](docs/decisions/0046-world-release-before-v2-migration.md)
  places that migration release after the World releases.
- **Commands beyond the accepted core** remain provisional: `archive plan`,
  `apply`, and `views`.

## What is open

The policy grammar, per-kind rules for "current", World's storage, expunging,
and Archive's adapter to Spool, among others. They are listed in
[docs/open-questions.md](docs/open-questions.md) and are open on purpose.

## Where to look

The [proposed shadow runbook](docs/shadow-runbook.md) describes the records-first
checkpoint and the separate approvals required before operating on a copy.

```
AGENTS.md            how to change this repository (for agents and people)
docs/vocabulary.md   what each term means
docs/invariants.md   what must always hold
docs/state-model.md  what counts as evidence, and what happens when things are lost
docs/provenance.md   claims, trust, and disagreement
docs/boundaries.md   how the parts depend on each other; what is left out on purpose
docs/shadow-runbook.md proposed records-first shadow procedure and approval gates
docs/decisions/      why each decision was made
spec/                the contract copied into every archive
docs/proposals/v2-migration-release-readiness.md
                     proposed V2 release boundary and unresolved audio gates
relay/               HTTP for the operator's browser: Door's service, World's query, derived media
world/               experimental, rebuildable semantic projection
world/projection/    what each World projection version emits: SHACL shapes and vocabulary
fixture/             published synthetic records and the builder that admits them through Door
enrich/              enrichment tools, the adapter that proposes their runs to Door, and derived media
capture/             the native importer of recorder files: grammars, sessions, and the enrichment chain
```

## Contributing

Read [AGENTS.md](AGENTS.md); it applies to people too. Find the concept your
change serves. If the change needs an invariant broken or an open question
answered, propose a decision record first. Run `make check`. Sign off each
commit (`git commit -s`) to certify it under the
[Developer Certificate of Origin](https://developercertificate.org).

The Nix flake pins the Nixpkgs release used to build and test the Haskell
package. Run `nix develop` for its compiler and command-line tools, `nix build`
to build Archive and run its Cabal test suites, or `nix flake check` to run the
same build as a flake check. `make check` remains a separate repository
structure check.

## Licence

The code is licensed under the
[GNU Affero General Public License, version 3 or later](LICENSES/AGPL-3.0-or-later.txt).
The specification, contracts, and documentation are dedicated to the public
domain under [CC0 1.0](LICENSES/CC0-1.0.txt). `REUSE.toml` says which files
are which. Archives made with Archive are not covered by either: their content
is their owner's ([ADR 0017](docs/decisions/0017-licensing.md)).

Copyright the Archive contributors. Started by William Britt Mathis.
