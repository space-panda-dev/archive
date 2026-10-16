# 0007. Policy declares desired state

Status: accepted

The archive needs one place where the operator says what should be true (how
many copies, which enrichments, what gets published) without writing code, and
that place must stay safe and readable for decades.

## Decision

**Policy is desired state; mechanism is code.** "Keep accepted WAVs in two
failure domains" and "transcribe accepted rehearsal audio" are policy.
"`b2_bucket = ...`" and "use large-v3 on this machine" are configuration.
"Chunk audio longer than 90 minutes" is code. When a request would add a special
case to code, first ask whether it is really a desired state; when it would add
a feature to the policy language, first ask whether it is really mechanism. The
policy language stays deliberately less powerful than the code.

**What policy covers.** Policy produces two kinds of output:

- **desires** that apply carries out: placement, derivation, review
  requirements, and publication under an existing approval;
- **recommendations** that only a person acts on: candidates for retirement or
  destruction.

Preservation planning is part of policy's job. Format identification is an
enrichment producing a `format` claim with a PRONOM identifier; rules such as
"every original in a proprietary format has at least one open-format rendition"
are policy; migration is derivation, and the original is kept.

**Human-authorized.** Anyone may write a policy proposal: the operator, a
model, a program. Proposals live outside the archive until the operator
authorizes one; Door then admits it as a new revision, recording author and
authorizer separately. Machines never change authoritative policy.

**Semantics.**

- **Rules only add.** When several rules match, their desires combine; numbers
  such as "at least N failure domains" take the largest. Rule order never
  matters.
- **No rule overrides another.** An exception that lowers a value is written
  into that rule's own condition.
- **`not` only in conditions,** over evidence, in a form that cannot loop, and
  written so that a missing fact leads to the more protective outcome.
- **Evidence only.** Rules read the archive's own records. A fact from World is
  never read during evaluation; the plan proposes promoting it, and once Door
  has admitted it as a claim, a later evaluation can use it.
- **Unknown never reduces.** A fact that cannot be established never causes a
  dropped copy, a retirement, or a withdrawal. A missing observation is unknown,
  not zero.
- **Derivation names claim kinds,** not tools, optionally with constraints such
  as a minimum contract version. Configuration maps kinds to tools. Whether a
  tool change requires re-deriving is set per kind.
- **Rules have names,** and every transition policy causes cites the policy
  revision, the rules that fired, and the facts they matched.
- **Door refuses** a policy revision that does not parse, overrides a rule, or
  uses `not` in a way that could loop.

**Language.** Policy is written in a small subset of standard Datalog (rules,
safe negation, a maximum), defined by the specification together with test
fixtures, so that any future engine can prove it gives the same answers. clingo
is today's engine, behind an adapter, called from a Python planner. Door checks
policy revisions itself in Haskell and never evaluates policy.

## Rejected

- **Defaults-then-exceptions.** Easier to write, but a rule's effect would
  depend on the whole file, and "why is this wanted?" would have no complete
  answer.
- **An engine-specific dialect** (clingo's own syntax, Rego) or rules written in
  Haskell. The archive must outlive its engine, and policy must not become code.
- **A bespoke evaluator.** It would mean writing a query engine.
- **Retention as something apply carries out.** Deciding what goes is a
  person's act.
