# 0126. Fleet applies a pinned Archive upgrade plan

Status: accepted (operator, 2026-10-28).

ADR [0068](0068-specification-upgrade.md) makes a specification upgrade one
validated Door transition. The operator approved a bounded boundary with
fleet (fleet ADR 0017): fleet owns package selection, explicit operator apply,
stopping writers, starting services and health verification; Archive owns
release validation, source freshness, the Door proposal and an operational
receipt. Fleet never synthesizes transition records or changes archive files.
Archive never controls services or activates a deployment.

The pinned package exposes `archive-upgrade plan` and `archive-upgrade apply`.
Planning reads a private configuration and exact packaged release, verifies
the source archive and operator, and asks the real Door to dry-run the exact
upgrade. Its JSON plan binds the semantic configuration, archive identity,
HEAD, current specification, current operator revision, complete release bytes
and complete contract pins. A refused dry run produces a blocked plan, never
ready. A same-version plan verifies exact installed specification and contract
bytes before reporting no-op. Fleet compares release pins with its own
configured expectations before offering an operator apply.

Apply requires the reviewed plan digest and source fingerprint. It recomputes
both and refuses stale inputs before entering Door. The transition proposal
additionally carries the optional `expected_snapshot` guard: Door compares it
with the exact source HEAD under its existing write lock before a new
transition mutates evidence. Only a complete identical retry bypasses this
source guard. The optional field preserves existing transition clients. A successful apply commits
only the predicted specification-upgrade transition. Its existing contract
and deterministic identity bind the original source fingerprint; an identical
retry reconstructs that original source from the transition's introducing
commit, validates the same plan and asks Door to recognize the exact retry.
This adds no durable record shape. The plan and receipt are operational;
Door's existing upgrade transition remains the durable evidence.

The receipt distinguishes no durable mutation, a confirmed committed upgrade,
and an unknown outcome. A lost response after entering Door is unknown until
an exact retry resolves it. A later verification failure preserves the known
committed outcome. Fleet must not treat a failed or unknown receipt as proof
that restarting writers is safe. No checkpoint, rollback, backup or rehearsal
protocol is introduced by this boundary; none is implied by this approval.

The public interface and digest definitions are in
[the command reference](../archive-upgrade.md). Synthetic real-Door tests run
the detached package command, prove read-only planning and stale refusal,
exercise additive upgrade and quiet retry, and preserve truthful receipts
when a committed response or later verification is lost.
