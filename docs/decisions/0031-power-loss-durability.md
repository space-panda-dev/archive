# 0031. Door acknowledges only durable commits

Status: accepted

Door currently makes an atomic Git ref change for a metadata batch, but an
atomic ref change alone does not say what survives sudden power loss. A caller
can receive `committed` while recently written record or Git bytes are still
only in volatile caches. ADR 0023 specifies ordinary failure and retry but
does not define this durability boundary.

## Decision

**A successful persistent Door response promises a recoverable complete
commit after power loss** on a local filesystem that honors synchronization
requests. Door must synchronize the record bytes, containing directories, Git
objects and ref needed to read that commit before reporting success. If a
required synchronization fails, Door must refuse success and report the
uncertain outcome; it must not assert that the batch was rolled back if the ref
may already have advanced. An exact retry checks the committed tree before
doing anything else and returns the original stored revisions and recording
times when the whole batch is present.

Before the ref advances, an interrupted operation may leave only operational
files and uncommitted record files. The next Door mutation, under the archive
lock, must reconcile those files against the pinned old and current commits.
It may remove only files it can prove it installed for that operation and that
are absent from committed history. A mismatch or incomplete proof is a
refusal requiring operator inspection, never a cleanup guess. Recovery must be
idempotent and must not change committed archival meaning. After the ref
advances, the committed batch is authoritative even if the response was lost;
recovery finishes operational cleanup and an exact retry stays quiet.

Content-bearing admission has the same success promise. Door must establish
that git-annex's locally verified content and the committed content path are
durable before acknowledging the admission; a transfer or location log alone
is insufficient. Until that mechanism is demonstrated, content-bearing
admission remains unsupported. This decision does not claim that a remote
copy, an unverified staging host, or a filesystem that lies about sync is
durable.

## Why

The acknowledgement is the point at which an operator may discard a proposal
or source. Returning success before the records and the bytes they name are
recoverable would make that action unsafe. The recovery rule also preserves
INV-5: retrying after interruption must not create a second history event.

## Alternatives rejected

- **Promise only process-crash recovery.** That leaves a successful response
  vulnerable to a power outage and makes the acknowledgement unsuitable for a
  source-clearance decision.
- **Treat a leftover record filename as a completed admission.** Only Door's
  committed Git tree carries archival meaning (INV-2); a loose file does not.
- **Delete every unexpected file on startup.** It could erase an operator's
  unrelated data or evidence of an incomplete operation.

This decision is accepted; implementation and a new specification release are
still required before Door can claim this success contract. The immutable 0.0.1
and 0.0.2 resources stay unchanged. Draft normative wording for the next release
is in [the specification amendment](../proposals/0031-power-loss-spec.md).
Until the implementation proves the synchronization and recovery boundaries,
current persistent commands must not be represented as power-loss durable.
