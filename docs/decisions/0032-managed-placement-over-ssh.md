# 0032. Managed placement names a location, not a host

Status: accepted

ADR 0009 identifies the seedbox as a managed offsite copy, but does not define
how apply reaches it. A hostname is a transport address, not the identity of
a location; it can change without changing custody or the failure domain.
This decision defines a narrow placement operation without deciding policy's
still-open grammar or treating publication as byte transport.

## Decision

**A managed location has a stable opaque identity separate from its SSH
endpoint.** Operator-controlled configuration binds that identity to its
transport endpoint, authenticated SSH host identity, git-annex repository
identity, allowed command, managed root, and failure domain. The endpoint and
path are mechanism; the desired content and destination are policy. Changing
an endpoint does not silently create a new location or change its failure
domain. A changed repository or SSH identity requires an explicit
reconciliation before apply can use it.

The primitive placement intent is **ensure content identified by digest and
size is present and verified at managed location Y**. A bound plan names the
content, target location, policy revision, evidence snapshot, and preconditions
for the action. Apply rechecks them, selects a verified available source, and
uses git-annex's transport and location accounting. It does not route bytes
through Door. An optional source in a plan is a constraint that apply must
recheck, not authority to trust the source's claim. A plan never names a
hostname, shell command, or path derived from an untrusted identifier.

Availability advertised by a location is a hint. It is operational evidence,
bound to the location and content identities and its observation time, and is
not an archival record or proof of fixity. Apply independently verifies digest
and size at the source before transfer and at the destination after transfer.
For an external location it uses a narrowly authenticated remote capability
whose exact argv and managed root come from trusted configuration. It never
assembles a shell command, accepts option-like user values as options, follows
untrusted path components, or treats SSH success or an annex location log as
digest verification. Failure, stale evidence, ambiguous identity, and
interrupted transfer leave placement incomplete; retry probes actual presence
and fixity and writes no new history when nothing changed (INV-5).

This operation **only adds or verifies presence**. It cannot drop a copy,
delete a remote file, publish a symlink, or create a public reference. A
separate reduction must satisfy ADR 0008's fresh-plan and surviving-domain
checks, and destroy remains a recorded human decision under ADR 0004.
If a destination is outside the operator's custody and can see plaintext,
ADR 0016 requires a granted, recorded disclosure before transfer; an
encrypted remote whose provider lacks the key is treated according to that
ADR. Location evidence may support a copy count only under ADR 0009's
managed-location and failure-domain rules, never merely because bytes are
visible through a projection.

**Publication is a separate, policy-authorized transition using placement.**
An approved exact version may require managed presence and then a projection
or public reference, but the latter step is not the meaning of `ensure`.
“Link this from here to there” is therefore not an operation: annex presence,
a location observation, and an exposed reference are distinct outcomes. For the
seedbox, the music folder is outside the git-annex repository. A future
authorized publication may project already placed content into that folder;
the projection is not another managed copy and cannot satisfy a copy count.
No public or cross-custody placement is implemented until its authorization,
disclosure record, and withdrawal semantics are accepted. The initial
implementation is limited to private managed copies controlled by the
operator.

## Why and limits

This gives the seedbox the same identity and placement path as any managed
location. The narrow additive operation can be retried after interrupted SSH
or git-annex work without making deletion or publication an accidental side
effect. It also leaves policy's choice of desired copies in policy (ADR 0008).

The policy grammar, failure-domain configuration format, verification
freshness window, disclosure record fields, publication transition contracts,
and a remote drop protocol remain open. This proposal does not close them.
The seedbox symlink/torrent behavior is separately gated by the
[managed-copy experiment](../seedbox-managed-copy-experiment.md).

## Alternatives rejected

- **Use a hostname as location identity.** Renaming or replacing a machine
  would silently change the meaning of recorded placement evidence.
- **Make `publish` a separate transfer path.** It would duplicate fixity,
  retry, and security mechanisms while conflating copy presence with exposure.
- **Count a symlink or public URL as a copy.** Both are projections and fail
  ADR 0009's failure-domain rule.

This decision is accepted; the remote protocol remains unimplemented and
requires a new specification release. Draft normative wording for that release
is in [the specification amendment](../proposals/0032-placement-spec.md).
The proposed first private whole-Archive copy profile, including the seedbox's
boundary from the separate music publication folder, is recorded in
[proposed ADR 0037](0037-proposed-private-managed-copy.md). It does not close
the listed configuration and protocol questions.
Proposed ADR 0038 chooses a JSON configuration and control protocol and defines
the read-only verification and whole-Archive copy boundary; it remains
proposed pending the required synthetic probe and specification release
([0038](0038-proposed-private-placement-protocol.md)).
