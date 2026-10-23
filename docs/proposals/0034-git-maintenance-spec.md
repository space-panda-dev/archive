# Draft specification amendment: coordinated Git maintenance

This is draft wording for a release after `archive/0.0.2`. It is not part of <!-- lens: allow stale -->
any archive's current specification. It depends on accepted
[ADR 0034](../decisions/0034-git-object-store-coordination.md) and the accepted
[durability amendment](0031-power-loss-spec.md).

Add to S-4a:

> Before reporting a successful persistent mutation, Door MUST establish a
> stable, complete, local Git object-store baseline for the committed history
> on which the new commit depends. Git object-store maintenance MUST coordinate
> with the archive lock held by Door during that operation. Door MUST refuse
> success when the object store is borrowed, shallow, promisor-dependent, or
> otherwise cannot be proven complete and durable. A failed baseline check or
> synchronization before ref advancement MUST prevent the ref update. After
> ref advancement, failure to establish the durability barrier MUST be
> reported as an uncertain outcome.

Conformance fixtures must cover a packed baseline, a loose-object baseline,
operator maintenance serialized by the archive lock, refusal of unsupported
object-store topology, and a failed synchronization before and after ref
advancement. A process-kill test alone does not establish power-loss durability.
