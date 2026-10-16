# 0058. Door's refusals carry stable codes

Status: accepted

Door's service (ADR 0054) answered every failure as `{"ok": false, "error":
"<text>"}`. The text came from `mutationErrorMessage`, which flattens
MutationError's categories, and from the planners' plain-text refusals. A
client could not tell a proposal Door refuses from a stale expected head it
should rebuild, from a disk failure it should report, or from a write whose
outcome is unknown. It could only match message text, which is not a
promise. A client built by someone else, and tested in their own CI, needs a
promise.

The operator decided on 2026-10-15 that Archive's boundary stays strict and
general: what Door's service answers is a published promise to any client.

## Decision

**Every failed answer carries a code and a message.** The service answers
`{"ok": false, "error": {"code": ..., "message": ...}}`, plus
`unadmitted_keys` when content was transferred before the failure. The code
is stable; the message is for people and may change.

**The codes:**

| Code | Meaning | What a client does |
|---|---|---|
| `request_malformed` | The service request itself is invalid: not JSON, wrong fields, unknown command, a line over 65,536 bytes, no newline, or nothing within 30 seconds. | Fix the client. |
| `proposal_malformed` | Door cannot read the proposal directory, manifest, or a draft as a proposal. | Fix the proposal. |
| `proposal_refused` | The proposal is well formed, but Door refuses it against this archive. | Change the proposal. |
| `expected_head_stale` | An expected head no longer matches the archive. | Re-read the heads, rebuild, send again. |
| `archive_unreadable` | Door could not read the archive's committed state. | Report it; nothing was written. |
| `integrity_failure` | Bytes or committed evidence did not verify. | Report it; nothing was admitted. |
| `configuration_failure` | Door's configuration, such as its Git or git-annex executable, is wrong or changed. | Report it. |
| `io_failure` | An operating-system read or write failed. | Report it. |
| `subprocess_failure` | A program Door runs, such as Git or git-annex, failed. | Report it. |
| `operation_failed` | A failure with no narrower code yet. | Report it. |
| `outcome_uncertain` | Door cannot tell whether the write committed. | Send the identical proposal again; a committed batch answers as a retry. |
| `internal_error` | Door failed unexpectedly. | Report it. |

**Codes come from types, never from messages.** Each MutationError category
maps to exactly one code. The planners now return a typed refusal that
separates a stale expected head from every other refusal, so
`expected_head_stale` is decided where the heads are compared. A failure
reading the proposal is `proposal_malformed`, a failure parsing the request
line is `request_malformed`, and an unexpected exception is
`internal_error`.

**The list is published and checked.** `door/service.schema.json` is a JSON
Schema 2020-12 description of the request line and every answer, including
the codes and their descriptions. Door's tests fail when the schema's codes
or descriptions differ from Door's, and the relay's tests validate every
answer Door's service gives them against the schema.

**Adding a code is the only change allowed.** A code is never renamed,
reused, or removed. A new code is added only when a client should act
differently from every existing code; it needs a line in this record's table,
in Door's type, and in the schema. Clients treat an unknown code as
`internal_error`. Narrowing a failure that today answers `operation_failed`
into a new code is an addition.

**The request line is enforced exactly.** The service previously parsed
whatever it had read once 64 KiB arrived, and waited indefinitely for a
silent client. It now refuses a longer line, a connection closed before its
newline, and a client that sends nothing for 30 seconds, each as
`request_malformed`. After answering, the service ends its side of the
connection and reads whatever the client still sends, for up to a second and
1 MiB, before closing: on Linux, closing with unread input resets the
connection and discards the answer, so a client sending an oversized line
would otherwise see a reset instead of its refusal.

## Consequences

- The relay passes Door's code through unchanged, so a browser client sees
  the same codes (ADR 0060).
- The command line keeps printing the message alone; codes are a service
  promise.
- A client written against the old string `error` must read `error.message`.
  The relay deployed before this change still expects the old shape, so Door
  and the relay are upgraded together.

## Alternatives considered

- **One code per message.** Several hundred codes, most of which no client
  would act on differently, and each one a promise.
- **Classify messages by pattern.** It turns message wording into a contract
  by accident, which is what this record replaces.
- **HTTP status codes inside Door.** Door's service is not HTTP (ADR 0054);
  the relay maps codes to statuses.
