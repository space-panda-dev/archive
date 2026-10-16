# 0037. Proposed whole-Archive copy scope

Status: proposed

**Custody blocker:** Proposed ADR 0038 sends the whole Git history and annex
content to the seedbox in readable form. ADR 0016 classifies a hosted plaintext
transfer as service disclosure, while accepted ADR 0032 limits the initial
placement implementation to private managed copies. The operator chose
plaintext service placement with authentication at every served seedbox
interface; [proposed ADR 0039](0039-proposed-hosted-plaintext-placement.md)
records the needed cross-custody change. The existing transfer of selected
music segments grants no authority for the entire Archive. Until ADR 0039 and
the dependent disclosure contracts are accepted, this proposal remains
blocked for the seedbox.

ADR 0032 accepts a generic managed location and a narrow SSH capability, but
its remote protocol is unimplemented and requires a later specification
release. The program configuration format and failure-domain configuration
remain open. Those boundaries block an implementation that loads location
configuration or invokes a remote capability. The first intended placement
target is the seedbox, where the desired result is a managed copy of the
whole Archive, not a copy of only the already published music segments.

## Proposed decision

The first SSH placement implementation MUST be driven by
operator-controlled configuration and MUST fail closed unless a configured
location binding is complete and unambiguous. Configuration format and secret
storage remain separate decisions; this proposal does not select a file format
or credential store.

A location binding MUST include a stable opaque location identity, transport
endpoint, authenticated SSH host identity, remote git-annex repository
identity, fixed capability, managed root, and failure domain. The endpoint may
change without changing location identity only when the authenticated host
identity and repository identity still match. A changed host or repository
identity MUST block placement until the operator reconciles the binding. A
hostname, directory path, annex key, or publication URL MUST NOT serve as the
location identity.

The SSH invocation MUST use a fixed executable and trusted configuration,
authenticate the configured host identity, and invoke only a fixed remote
capability. It MUST NOT build a remote shell command from record fields, paths,
digests, endpoint text, or other untrusted values. Requests MUST use a
structured channel with a closed schema; the remote capability MUST validate
every value and confine filesystem access to its configured managed root.
Option-like values, shell metacharacters, path traversal, ambiguous host keys,
and substituted annex repository identities MUST be refused before transfer.
The exact request schema, executable resolution, SSH options, and remote
capability installation procedure require the specification amendment and
implementation design; this proposal does not authorize any remote change.

An ensure-presence action MUST be bound to a committed source snapshot and
identify the target location and exact content digest and size. For a
whole-Archive copy, the source snapshot MUST also identify the Archive ID,
specification version, Git refs and commit, and the complete set of annex keys
required by that committed state. Completion requires the matching Git state
and every required annex object to be independently verified at the target.
Missing records, keys, or verification results leave the action incomplete.
The copy MUST preserve the source Archive identity and MUST NOT make content
eligible for destruction at the source.

The seedbox copy covers Archive's managed Git repository and git-annex content.
It does not include a music publication folder outside the annex repository.
That folder remains a projection under ADR 0032, carries no managed-copy
credit, and is not evidence that the whole Archive was copied. Creating or
refreshing that projection remains a separate publication action.

Before content can leave the operator's custody in readable form, apply MUST
require the applicable grant and disclosure record under ADR 0016. A
classification of the seedbox, its provider, and its access to plaintext MUST be
established before a real transfer. The implementation MUST add or verify
presence only; it MUST NOT drop, publish, withdraw, or delete. Failure and
interruption remain incomplete. Retry MUST probe and verify actual state and
must account for any git-annex bookkeeping writes under INV-5.

## Required synthetic conformance cases

Before real bytes are transferred, synthetic tests MUST demonstrate:

- a hostname change with the same host and repository identities retains the
  configured location, while a substituted key or repository identity blocks;
- malformed, missing, or ambiguous configuration fails closed;
- fixed SSH argv and request encoding reject option injection, shell syntax,
  path traversal, and unexpected fields without invoking SSH;
- a full-copy manifest includes committed Git state and every required annex
  key, and cannot pass when any record, key, or verification is missing;
- a publication-folder projection is excluded from managed-copy counts;
- source and destination digest mismatches, interrupted transfer, and exact
  retry remain incomplete until reverified;
- apply cannot drop content or proceed without the required disclosure grant
  and record; and
- an unchanged retry writes no new record, commit, or git-annex location
  entry, accounting for git-annex commands that update operational metadata.

Proposed ADR 0038 supplies a concrete configuration encoding, SSH binding,
control protocol, inventory, retry, and verification contract for these cases.

All tests use synthetic identities and disposable local repositories. A
successful synthetic probe is a prerequisite to preparing a separate runbook
for the real seedbox copy; it does not authorize remote configuration changes or
transfer of real archival bytes.

## Open boundaries

This proposal does not resolve the general program configuration format, the
failure-domain membership procedure, verification freshness, or SSH capability
deployment. It does not specify publication or withdrawal. The open questions
in [open-questions.md](../open-questions.md) remain open until this proposal is
accepted and each dependent boundary has a concrete answer.
