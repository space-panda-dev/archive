# 0114. A local probe reads the relay's liveness, commit and build without a tailnet identity

Status: proposed on `readiness-adopt`; amends
[ADR 0056](0056-tailnet-relay.md) and [ADR 0060](0060-relay-interface-and-role.md)'s
`/health`

## Context

Every relay request must carry an allowed `Tailscale-User-Login` (ADR 0056).
That is right for anything that reads the archive, but it also hides the one
answer a process on the archive machine needs to supervise the relay: is it
up, and which code is it running. A read-only fleet observer, a service
manager's health check and a development tool that starts a relay on a free
port for one worktree all ask `curl http://127.0.0.1:PORT/health` without a
tailnet login, so they get 403 and cannot tell a running relay at a stale
commit from one at the current commit.

ADR 0056 already notes that a process on the archive machine that can reach
the loopback port can claim any allowed login. A local probe therefore gains
nothing it could not already have; the question is only what the relay
answers when nobody claims a login.

## Decision

**A local probe gets liveness only.** A `GET /health` from a loopback peer
that carries no `Tailscale-User-Login`, no other `Tailscale-*` header, no
`Origin`, and no `X-Forwarded-For` or `Forwarded` is answered

    {"ok": healthy, "healthy": healthy, "writes": "dry-run" | "commit", "commit": ..., "build": ...}

with 200 when Door and World both answer and 503 otherwise, as ADR 0060's
`/health`. It has no `archive` block: no archive identifier, specification,
role, projections, caller, circle, or World snapshot. Every other route, and
a `/health` that names a login the relay does not allow, is refused as
before (`forbidden_identity`).

**Forwarded requests are never local.** `tailscale serve` forwards tailnet
requests to the loopback port, so the peer address alone cannot tell a local
probe from a tailnet caller without a user login (a tagged device). The
headers a proxy or a browser adds decide it instead: any one of them makes
the request an ordinary one, which needs an allowed login. The answer is
also chosen so that reaching it through a proxy would disclose nothing
archival or personal: the code's commit is public, and whether Door answers
says nothing about the archive's content.

**Health names the commit and build.** Both `/health` answers carry
`commit` and `build`, each a string or null. The relay reads them, in order,
from `ARCHIVE_COMMIT` and `ARCHIVE_BUILD` (the Nix wrapper sets the flake's
revision and the output's name); from `relay/commit.txt`, which `git
archive` stamps through `export-subst` (build `export`); or from the
checkout it runs in (`git rev-parse HEAD`, build `checkout`, or
`checkout-dirty` with tracked changes). What none of them says is null; the
relay never guesses.

The internet listener of [ADR 0092](0092-internet-exposure.md) is reached
through a tunnel that also connects locally, so it keeps its own rule for
`/health`; this decision is the tailnet listener's.

## Consequences

- Supervision reads the relay without a login: `curl -s
  http://127.0.0.1:PORT/health` says whether it answers and which commit it
  runs.
- `relay/openapi.json` describes the second answer (`LocalHealth`) and the
  two new `Health` fields; the relay's tests validate both.
- A dirty checkout says so, but not what changed.

## Alternatives considered

- **A separate unauthenticated port for health.** A second listener to
  configure, and to publish by mistake, for one answer.
- **A configured login for supervisors.** Every supervisor would need the
  login and its agent in the relay's configuration, and would be counted as
  a caller in logs; for liveness that is ceremony without a defence, given
  ADR 0056's note on local processes.
- **The full answer to a local probe.** It would name the archive and its
  World snapshot to anything that reaches the port through a proxy without
  a login.
