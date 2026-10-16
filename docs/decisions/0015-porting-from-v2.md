# 0015. Porting from the V2 spike by translation

Status: accepted

A predecessor project produced a working "V2" spike: a Haskell Door, a portable
record format, git-annex placement, enrichment tools, a task spool, and a
migration that accounts for every production record. It is tested against real
data. It also uses different words and makes some choices this constitution
rejects.

## Decision

**This repository is the semantic authority; V2 is reference material.** Its
mechanisms are taken, but its names, verbs, and meanings are translated into
this vocabulary. Where V2 conflicts with the constitution, the constitution wins
unless a decision record changes it. Where V2 was right and the constitution
wrong, the constitution is corrected; separating object from content identity
came from V2.

**Known translations:**

| In V2 | Here |
|---|---|
| `rec-add`, `rec check`, the V2 init command | `door admit` / `door transition`, `archive doctor`, `archive init` |
| ingest | admit |
| `decision` shape; `action_permission` | assessment; dropped |
| review states inside claims | assessment records (ADR 0005) |
| location and annex key in the artifact record | location outside the record (ADR 0003) |
| facts copied into git-annex metadata each run | placement labels written only on change (ADR 0008) |
| "current" rules in Haskell | rules in contract data (ADR 0003) |
| `by` only | `by` and `agent` |
| provenance repeated in payloads | provenance in the envelope only |
| records never deleted | retire and destroy (ADR 0004) |
| retire by bare supersession | a retire claim (ADR 0003) |
| approval as a `decision` | a transition claim (ADR 0003) |
| policy in a `policy.rules` file | an artifact adopted by a `policy_revision` claim |
| `revision_id` over aeson output, without `spec` | SHA-256 over RFC 8785, with `spec` (P-3) |
| `policy` writing git-annex metadata while planning | labels written by apply (ADR 0008) |
| the task spool | Spool, ported to its own repository |

**Every V2 component gets a disposition:** keep its mechanism, translate it,
rewrite it, delete it, or defer it. Defer means the constitution does not yet
define what the component decides; the port leaves a hole that fails loudly
("unsupported: the contract defines no current rule") rather than reproducing
V2's answer. Feature parity with V2 is not the goal; conformance is. The ledger
of dispositions is kept while the port runs and deleted with this record.

**V2 never answers an open question.** When a slice of the port needs one
answered, the slice stops and the dependency is reported. "V2 had it" justifies
nothing: every piece ported points to a concept, an invariant, a specification
requirement, or a mechanism something needs. Known places V2 knows more than
the constitution:

- **"Current" rules.** Port the interface, not V2's per-kind semantics; the
  policy grammar and each kind's rule are open.
- **Placement.** Keep the git-annex mechanism; leave the desired state out until
  the policy language exists, rather than moving it into code.
- **On-disk layout.** V2's directories are the starting candidate, not the
  answer. Every path is built in one module, so the layout stays changeable.
- **Admission and staging.** The constraints in ADR 0002 hold (bytes stay where
  they are, their hash is computed there, Door verifies presence and admits);
  V2's exact commands and files are provisional.

**Sequence.** The V2 migration of the existing archive is completed in V2 first
and ported here afterwards. V2's `policy` command must never run against a
git-annex repository that will be kept, because it rewrites replicated history.
Bytes are brought across with `git annex reinject`, never by adding V2
repositories as remotes. The version here starts at 0.0.1.

This record can be retired once the port is complete.
