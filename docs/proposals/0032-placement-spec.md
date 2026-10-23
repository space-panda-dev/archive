# Draft specification amendment: managed placement

This is draft wording for a release after `archive/0.0.2`. It is not part of <!-- lens: allow stale -->
any archive's current specification. It implements accepted
[ADR 0032](../decisions/0032-managed-placement-over-ssh.md).

Add a managed-placement section to the format and protocols section:

> A managed location MUST have a stable identity distinct from its transport
> address and filesystem paths. Operator-controlled configuration MUST bind
> that identity to an authenticated endpoint, a git-annex repository identity,
> an allowed capability and managed root, and a failure domain. Apply MUST
> refuse a changed or ambiguous binding rather than silently substitute a
> different location.
>
> A placement plan action MUST identify exact content by digest and size, a
> target location, the authorized policy revision and evidence snapshot, and
> the conditions apply must recheck. The action to ensure presence MUST NOT
> imply deletion, withdrawal, publication, or a public reference. Apply MUST
> refuse stale or unprovable preconditions. Source and destination fixity MUST
> be verified independently; advertised availability and git-annex location
> entries alone MUST NOT establish fixity.
>
> An SSH transport MUST use an authenticated, narrowly scoped capability,
> fixed executable and argument boundaries, and paths derived only from
> trusted configuration and validated content identities. It MUST NOT
> evaluate shell syntax or interpret untrusted identifiers as options or path
> components. A failed or interrupted transfer MUST remain incomplete until
> reverified. An unchanged retry MUST NOT write a new record, commit, or
> git-annex location entry. An ensure-presence action MUST NOT drop any copy.
>
> Before plaintext can leave operator custody, the required disclosure grant
> and record MUST exist. Publication requires its separate approved transition
> and any applicable rights and sensitivity checks before exposing a reference.
> A projection or public reference MUST NOT count as a copy. On the seedbox,
> a future music-folder projection is outside its git-annex repository and
> remains separate from the managed copy and its location evidence.

Conformance fixtures must cover a changed hostname with stable identity, a
substituted SSH or annex repository identity, stale and forged availability,
source and destination digest mismatch, killed transfer and exact retry,
option injection, shell metacharacters, traversal, unauthorized deletion,
missing disclosure, and an unchanged apply. The test destination must be a
disposable namespace; the actual seedbox requires separate authorization.
