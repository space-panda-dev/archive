# 0060. The relay publishes its interface and its role

Status: accepted

The relay (ADR 0056) was described only by its source and a README table.
Every refusal was `{"ok": false, "error": "<text>"}`, and most of Door's
answers became 422 "Door refused: …" whatever the cause. `/health` answered
`{"writes": …}` and 200 even when neither Door nor World answered. Exceptions
the relay did not expect dropped the connection, including after Door had
committed. A client could not tell a scratch archive, where committing is
safe, from the shadow or the real archive, except by its URL.

The operator decided on 2026-10-15 that the relay's promises are made to any
client, that clients refuse to commit anywhere but a scratch archive, and that
the role they check must not depend on the port or URL.

## Decision

**The interface is described in OpenAPI 3.1.** `relay/openapi.json` gives
every route, request body, answer, and status code, with JSON Schema 2020-12
for each body, so a client can generate code from it. The relay's tests
validate every answer they receive against it, check that its routes and
methods are exactly the relay's, and check that its codes are exactly the
relay's and Door's.

**Every answer has one shape.** Success is `{"ok": true, "archive": …,
"result": …}`. Failure is `{"ok": false, "error": {"code": …, "message": …},
"archive": …}`. `archive` is present for every allowed caller and absent
for a refused identity or origin. It names:

- `archive_id` and `spec`, from the archive's manifest;
- `role`, fixed in the relay's configuration;
- `projections`, the World projection versions served (ADR 0059).

**Refusal codes are stable.** Door's codes (ADR 0058) pass through unchanged.
The relay adds its own: `request_malformed`, `forbidden_identity`,
`forbidden_origin`, `agent_mismatch`, `commits_disabled`, `not_found`,
`method_not_allowed`, `length_required`, `body_too_large`,
`unsupported_media_type`, `content_not_relayed`, `query_invalid`,
`query_refused`, `query_too_many_rows`, `projection_not_served`,
`query_timeout`, `world_unavailable`, `door_unreachable`, `door_failed`, and
`internal_error`. Each code has one HTTP status, which the description lists.
A stale expected head is 409, a refused proposal 422, and an unreachable Door
503. The rule for adding a code is ADR 0058's.

**A commit's outcome is never lost.** If Door's service stops answering after
the relay has sent a commit, the answer is `outcome_uncertain`: send the
identical proposal again and Door's retry gives the outcome. Before the
request is sent, it is `door_unreachable`, and nothing was written. Any other
unexpected failure is answered as `internal_error`, never as a dropped
connection. If Door committed but World did not refresh, the answer is
success with `world.status` `stale` and the reason. Queries then answer from
the earlier snapshot, which each answer names, until a refresh succeeds.

**The role is configuration.** `role` is one of:

- `shadow`: a disposable migration copy that must stay faithful;
- `scratch`: a copy made for testing real commits (ADR 0061);
- `live`: the real archive.

It is required. A `shadow` relay must be `dry-run`. Clients refuse to send a
commit unless `role` is `scratch`; the relay's `writes` setting is a second,
independent guard.

**Health reports what answers.** `/health` checks that Door's socket accepts
a connection and that World's selected generation opens. It answers 200 only
when both do, and 503 otherwise, with the same body naming which one failed.

**Queries name their projection.** `/world/query` takes
`{query, projection, rdf_format?}`; the answer is
`{snapshot, projection, media_type, data}`, where `data` is SPARQL Results
JSON, or for a graph answer N-Triples (the default), Turtle, or JSON-LD as
`rdf_format` asks (ADR 0059). The format is a body field, not the `Accept`
header, because every relay answer is the same JSON envelope.

**Logs keep no login.** The relay logs the caller's agent identifier, not its
tailnet login, and a request line it cannot parse is answered, not crashed
on.

## Consequences

- The deployed relay predates this record. Door and the relay must be
  redeployed together, and a client written against the old string `error`
  and the old query answer must change.
- A client's CI can run the relay from Archive's release image against the
  fixture archive (ADR 0062), validate what it receives against the same
  description, and refuse to commit unless `role` is `scratch`.

## Alternatives considered

- **RFC 9457 problem details for errors.** A standard shape, but its `type` is
  a URI per problem, and Door's service is not HTTP. One code-and-message shape
  across Door and the relay is simpler for clients. Problem details remain an
  option with their own record.
- **The SPARQL 1.1 Protocol for queries.** Any SPARQL client could then query
  World unchanged. Its answers carry no snapshot, projection, or archive
  block, so it would need headers for those. It is left as an addition for
  the operator to decide.
- **Infer the role from the port or host name.** A copied configuration would
  carry the wrong meaning silently.
