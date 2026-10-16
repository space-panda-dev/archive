# 0056. A tailnet relay for the operator's browser

Status: accepted; media routes added by [ADR 0069](0069-derived-media.md)

The rehearsal review application runs in a browser on the operator's iPad and
phone. Door's service listens only on a private Unix socket (ADR 0054), and
World answers queries only from a local command (ADR 0041). Both records say
that reaching them from anywhere else needs its own decision. A browser cannot
open a Unix socket, name a proposal directory on the archive machine, or run
World's command.

The archive machine reaches the operator's other devices through a tailnet.
It accepts tailnet connections only through `tailscale serve`, which
terminates HTTPS with a certificate for the machine's tailnet name, forwards
to a port on the loopback interface, and adds the caller's tailnet login to
each request as the `Tailscale-User-Login` header.

## Decision

**A relay translates HTTP into Door's service and World's query, and nothing
more.** `relay/relay.py` listens on the loopback interface only and is
published to the tailnet by `tailscale serve`, never by Funnel. It owns no
state: every write is decided by Door, every read is answered by World's
selected generation, and a restart loses nothing.

**Writes become proposal directories.** A write request carries a proposal as
JSON: the manifest and each record draft it names. The relay writes them as
`manifest.json` and `records/...` into a fresh directory inside a private
staging directory, asks Door's service to act on it, returns Door's answer,
and removes the directory. Door retains the proposal bytes it read and checks
everything it checks for a command-line proposal (ADR 0023, ADR 0028). The
relay refuses an admission whose manifest lists content: the browser cannot
stage content in a git-annex remote, so content admission stays with
operator tools.

**Callers are tailnet identities bound to agents.** The relay's configuration
maps each allowed tailnet login to the object identifier of the person it
acts as. A request without an allowed login is refused. Every record draft in
a write must name the caller's agent as its `agent`; a draft naming anyone
else is refused before Door sees it. Only the operator's own login is allowed
today. Allowing anyone else is a disclosure to them (ADR 0016) and needs its
own decision.

**Browsers reach it only from named origins.** A request that carries an
`Origin` header must come from an origin listed in the configuration. Every
write and query must be sent as `application/json`, which a cross-origin page
cannot send without a preflight the relay answers only for listed origins. The
relay sets no cookies and reads none.

**Reads are read-only SPARQL over World's selected generation.** The relay
opens the selected generation read-only, so no update can run, and answers
`SELECT`, `ASK`, and `CONSTRUCT` queries. Each query runs in a worker process
with a time limit and a row limit; a query that overruns is stopped and
reported as such. Results are metadata projected from records. The relay
serves no content bytes.

**Commits are off until cutover.** The relay has two write modes. In
`dry-run` it passes only `admit-dry-run` and `transition-dry-run` to Door and
refuses a commit. In `commit` it passes commits as well and, after each one
that Door reports committed, refreshes World (ADR 0055) before answering, so
the answer names the snapshot that now contains the write. The relay runs in
`dry-run` against the semantic shadow. Switching to `commit` is a cutover step
with its own approval.

**The relay is an adapter, not a new concept.** It does nothing Door and
World do not already do; it owns no verb, leaves every invariant to Door,
and holds no authoritative state (the
[new-noun test](../vocabulary.md#adding-a-noun)). It is the application
adapter ADR 0041 anticipated, kept in its own directory because it depends on
both Door's service and World and belongs inside neither.

## Consequences

- The review application needs one URL on the tailnet for everything it reads
  and writes, and no database of its own.
- A write's cost is Door's cost for the batch, plus the incremental World
  refresh after a commit.
- Requests are handled one at a time. A long query delays a write by at most
  the query time limit. ([ADR 0063](0063-relay-read-speed.md) since runs reads
  side by side and keeps writes one at a time; a query no longer delays a
  write.)
- Another process on the archive machine that can reach the loopback port can
  claim any allowed login. The archive machine is the operator's own; a
  shared machine would need a stronger check.
- Audio for listening is not served by the relay. It stays with the existing
  content route until a decision covers streaming archive content.

## Alternatives considered

- **HTTP inside Door.** One process fewer, but it puts browser concerns
  (origins, request parsing, query time limits) in the one writer, which ADR
  0054 kept to a local socket.
- **A server inside the review application.** Archive's write gate would then
  live in a repository whose tests Archive does not run.
- **A bearer token as well as the tailnet identity.** It defends against other
  local processes, which the operator's own machine does not have, at the cost
  of a secret kept in the browser.
- **Named queries only.** Tighter, but every new screen would need an Archive
  change, and the reader is the operator, whose own data it is.
- **Commits against the shadow.** The shadow would stop being a faithful
  migration of V2, and its report would no longer be comparable.
