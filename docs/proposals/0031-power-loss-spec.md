# Draft specification amendment: Door commit durability

This is draft wording for a release after `archive/0.0.2`. It is not part of <!-- lens: allow stale -->
any archive's current specification. It implements accepted
[ADR 0031](../decisions/0031-power-loss-durability.md).

Add after S-4:

> **S-4a.** A successful persistent Door response MUST mean that the complete
> committed batch, including every record and any admitted local content it
> names, is recoverable after sudden power loss on a filesystem that honors
> synchronization requests. Door MUST refuse success if it cannot establish
> that guarantee. An interrupted operation MUST recover to either the prior
> committed state or one complete committed batch. An uncertain outcome MUST
> be reported as uncertain, not as a successful rollback.

Add to D-4 and the persistent transition procedure in D-9:

> Door MUST synchronize the record files, their containing directories, the
> Git objects, and the ref required to read the committed batch before
> reporting `committed`. For content-bearing admission, it MUST also establish
> durability of locally verified content and the committed content path before
> reporting `committed`. A failed synchronization MUST prevent a success
> response. An operation interrupted before ref advancement MAY leave
> operational files; the next mutation MUST reconcile them under the archive
> lock, removing only files proven to belong to that uncommitted operation.
> If that proof fails, Door MUST refuse mutation. If the complete batch was
> committed, an exact retry MUST return its stored revisions without writing
> new records, commits, or content-location entries.

Conformance fixtures must cover process kill and power loss at each record,
index, object, and ref boundary; exact retry after a lost response; failed
synchronization; mismatched recovery files; and preservation of unrelated
untracked files. The power-loss tests need a filesystem or fault harness whose
sync behavior is known; a process kill alone does not prove this contract.
