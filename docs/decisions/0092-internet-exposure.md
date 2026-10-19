# 0092. Internet exposure through Cloudflare Access; internet logins are members

Status: proposed (wave 1, drafted 2026-10-19)

The relay reaches people only over the operator's tailnet
([ADR 0056](0056-tailnet-relay.md)). `tailscale serve` terminates HTTPS,
forwards to a loopback port, and adds the caller's tailnet login as the
`Tailscale-User-Login` header; the relay maps that login to a person and
refuses anyone else. [ADR 0088](0088-bandmates.md) let bandmates in as circle
members, but only by giving each a tailnet login.

Three applications will read World and propose to Door through the relay:
the rehearsal review application, a lead-sheet songbook, and a sample
application. The people they are for, bandmates and friends such as a friend
who produces, should not have to join the operator's tailnet, which is the
network the operator's own machines share. They need to reach the
applications from any browser, with a login Archive does not have to run.

Three things stand in the way:

- The relay has one way to know who is calling, a header that only
  `tailscale serve` is trusted to set. A second way in must not let anyone
  set that header, or a forged one, and become the operator.
- Whoever carries the traffic over the internet terminates TLS and sees what
  is served in plaintext. [ADR 0016](0016-disclosure.md) makes that a
  disclosure to a processor, which policy must grant and which must be
  recorded before any content is sent (INV-25, S-32).
- ADR 0088 decides capacity by person: the operator's person is the
  operator, everyone else a member. Nothing says what a login is worth by
  the way it arrived.

The operator decided the following on 2026-10-19 (wave 1 decisions W1.1 to
W1.3); this record states it as given.

- **W1.1 Internet exposure.** The apps will be exposed to the internet through
  Cloudflare (Tunnel, so the host opens no ports) with Cloudflare Access
  login (one-time PIN or passkey; the person re-verifies on a new browser or
  when the session expires). The relay verifies the Access token (signature,
  audience, expiry) and maps the verified email to a person Entity, as it
  maps tailnet logins today; it refuses a request without a valid token.
  Archive never becomes an identity provider. One sign-on covers all three
  apps. Friends are not added to the tailnet.
- **W1.2 Cloudflare is a processor.** Cloudflare terminates TLS and sees
  everything served in plaintext, so it is recorded as a declared processor
  destination that policy grants (ADR 0016, INV-25), for everything members
  can read through it.
- **W1.3 Operator capacity stays on the tailnet.** A login through
  Cloudflare is always member capacity, including the operator's own.
  Operator capacity exists only over the tailnet.

## Decision

**The relay has two ingresses, told apart by the socket, never by a
header.** An **ingress** is the way a request reached the relay: `tailnet`
or `internet`. The relay listens on two loopback ports, one per ingress.
`tailscale serve` forwards to the tailnet port, as today; the Cloudflare
Tunnel connector on the archive machine forwards to the internet port and
to nothing else. The relay knows a request's ingress from the listener that
accepted it.

- The **tailnet listener** behaves as ADR 0056 and ADR 0088 say. It reads
  `Tailscale-User-Login` and ignores every Cloudflare header.
- The **internet listener** never reads `Tailscale-User-Login`. It requires
  a valid Access token, and every caller it admits has capacity `member`
  (W1.3), whoever they are.

**Why a forged identity header cannot reach it.** Both ports listen on the
loopback interface only, which `load_config` already enforces, and the
tunnel connects outward, so the host opens no inbound port (W1.1). From
outside the machine, the internet port is reached only through Cloudflare's
edge, after Access, and the tailnet port only through the tailnet. A
`Tailscale-User-Login` header sent through Cloudflare reaches the internet
listener, which does not read it. Whether the tunnel strips such a header
has not been checked, and this design does not depend on it. A forged
Access token, from a local process or from a path that bypasses Access,
fails signature verification. The relay verifies the token on every
request even though only the tunnel should reach the port. A local process
that reaches the tailnet port can still claim a tailnet login, as ADR 0056
already says; this record does not change that.

**The relay verifies the Access token itself.** Cloudflare's documentation
(read 2026-10-19) says Access sends the token to the origin in the
`Cf-Access-Jwt-Assertion` request header, signs it RS256, publishes its
keys at `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, and puts
`aud`, `iss`, `email`, `exp`, `nbf`, `iat`, `sub` and `type` in the payload.
For each request on the internet listener the relay:

1. reads the token from that header only, never from a cookie;
2. accepts only `alg` `RS256` and a `kid` among the keys it fetched from the
   configured team domain. It caches the keys and fetches again, at most once
   a minute, when it meets an unknown `kid`;
3. checks the signature, `iss` equal to the team domain, an `aud` among the
   configured audience tags, `exp` in the future and `nbf` in the past (with
   60 seconds of clock skew), and `type` `app`;
4. maps `email`, case-folded, to a person through the configured `logins`.
   `sub` is not used, because Cloudflare documents that it changes when a
   person is removed and added again.

Anything else is refused `forbidden_identity`, with no `archive` block, as
an unknown tailnet login is now. Archive keeps no session, password or
passkey and issues no token. One-time PINs, passkeys and the session length
are Access settings, outside Archive (W1.1). Whether Access accepts passkeys
as W1.1 assumes has not been verified here.

**One sign-on covers the three applications.** Each application has its own
hostname and browser origin, and all three reach the same internet listener.
The configuration lists one or more audience tags. That works whether Access
protects the three hostnames as one application or as three under one team
session; which of those Access supports has not been verified here. The
relay sets no cookies and reads none (ADR 0056); a browser application sends
the requests and Access adds the header.

**Configuration.** The relay's configuration gains an `internet` block
beside `callers` (which stay the tailnet logins):

```json
{
  "listen": {"host": "127.0.0.1", "port": 8450},
  "callers": {"operator-tailnet-login": "urn:uuid:…(the operator's person)"},
  "internet": {
    "listen": {"host": "127.0.0.1", "port": 8451},
    "processor": "internet edge",
    "team_domain": "https://example-team.cloudflareaccess.com",
    "audiences": ["…(an Access application's audience tag)"],
    "origins": ["https://review.example.org", "https://songbook.example.org", "https://samples.example.org"],
    "logins": {
      "ada@example.org": "urn:uuid:…(a bandmate's person)",
      "operator@example.org": "urn:uuid:…(the operator's person)"
    }
  }
}
```

- `listen` is a loopback address on a port other than the tailnet's.
- `processor` (1 to 200 characters) is the processor destination's name,
  exactly as the processor route below names it.
- `team_domain` is an `https` URL, and `audiences` are 1 to 8 distinct
  texts.
- `origins` lists the browser origins allowed on this listener only; the
  tailnet's `origins` stay separate.
- `logins` maps each allowed email to a person Entity. Each person must be
  an Entity of kind `person`, checked against World at start, as members
  are (ADR 0088). The operator's own email may be listed; it acts as the
  operator's person in capacity `member`.

A relay without `internet` opens no second port and is unchanged. Which
audiences an internet login may read is ADR 0093's decision. This record
requires only that every person in `logins` other than the operator be a
member of at least one audience.

**Capacity comes from the ingress.** `Caller` gains `ingress`. A caller's
capacity is `operator` only when the ingress is `tailnet` and the person is
the operator. Every internet caller is `member`, so the operator's internet
login cannot reach `/world/query`, `/door/transition`, or any other operator
route or contract (ADR 0088's `operator_only`). The `archive` block's
`caller` becomes `{agent, capacity, ingress}`. Logs keep the agent, never
the email, as they keep no tailnet login (ADR 0060).

**Cloudflare is a processor destination, granted and recorded.** Cloudflare
terminates TLS and could read every byte the internet listener sends: view
answers, documents, listening copies, stems and practice renders. It
computes on that content and shows it to no one else, which is ADR 0016's
processor. A hosted network provider is class `service` (ADR 0016) (W1.2).
That holds only while its terms do not let it keep or train on content, and
those terms have not been checked here (Open for the operator, 5).

A disclosure must be recorded before content is sent, naming the
destination, the content, and the policy revision (S-32). An audience's
readable set changes with every approval, and every one of those disclosures
also reaches Cloudflare. So the record is standing: a new transition says
that a destination is reached through a processor.

`archive.transition/processor_route` version 1:

```json
{
  "agent": "urn:uuid:…(the operator's person)",
  "payload": {
    "destination": "band relay",
    "processor": "internet edge",
    "class": "service",
    "decision": "route",
    "reason": "members read the band's audience through the internet"
  },
  "refs": [
    {"role": "authorizer", "id": "urn:uuid:…(the operator's person)", "revision_id": "sha256:…"},
    {"role": "policy", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

- `destination` (1 to 200 characters) names an audience's destination,
  exactly as its publication approvals name it (ADR 0088, ADR 0093).
- `processor` (1 to 200 characters) names the processor exactly as the
  relay's `internet.processor` does.
- `class` is the processor's exposure class. W1.2 makes it `service`.
  Version 1 accepts only `service`, so reclassifying the processor means a
  new contract version and a new route.
- `decision` is `route` or `end`, and `reason` is optional (1 to 2,000
  characters).
- `authorizer` is exactly one reference, pinning the current revision of
  the active operator's appointed person Entity, as a publication approval's
  does. `policy` is at most one reference, pinning a `policy_revision` Claim
  revision (Open for the operator, 4).

It is a transition, so `door transition` commits it and `door admit`
refuses it (ADR 0051). For each pair of destination and processor, the
route admitted last by Door's recording time is the one that holds. Door
records `destination` and `processor` exactly and does not check them
against configuration, as it records an approval's destination.

The disclosure record is then the route together with the approvals to its
destination. While a route holds, everything disclosed to the destination is
disclosed to the processor too, and that holds from the moment each approval
is committed. Like any disclosure record it may overcount, never undercount
(ADR 0016). Once a route ends, the relay sends nothing more through it.
Content sent while it held stays disclosed.

**The relay serves the internet only through a route that holds.** World
projects routes. The internet listener answers every request except
`/health` with `processor_not_granted` (403) unless a route holds for each
audience the caller reads and for the configured processor. An audience with
no route is not readable over the internet, even by its members.

**Over the internet, sensitivity is checked at service.** Content read
through Cloudflare reaches two parties: the member, who is circle, and
Cloudflare, which is service. Sensitivity limits every disclosure (INV-26),
so an internet caller's readable set is the audience's readable set (ADR
0088, ADR 0093) less every object whose furthest class by sensitivity, over
everything it pins, does not reach `service`. Those objects are shown
**withheld**, and the answer counts them (INV-24). Rights are still checked
at the circle. A processor shows content to no one, and ADR 0085's permit
table governs only who may be shown. `may_show` gains the ingress: a member
on the internet is shown an object only when both checks pass. The tailnet
ingress is unchanged. A `RED` object stays the operator's alone, and an
`AMBER` object reaches no internet caller (Open for the operator, 1).

**Destroy reports the processor.** A processor that received content is a
possible external survivor (ADR 0004, S-32). A destroy of an object reports
the processor of every route that held while the object was readable to that
route's destination. Access logs and anything Cloudflare caches are
Cloudflare's, so it is named, not cleaned. The relay keeps sending
`Cache-Control: no-store` on answers and `private` on media, which asks
shared caches not to keep them. Whether Cloudflare's edge always obeys
`private` has not been verified here.

**Refusal codes.** `processor_not_granted` (403: no route holds for this
ingress) is added under ADR 0058's rule. A missing, invalid, or expired
token is `forbidden_identity`, which already exists. The OpenAPI description
gains the internet listener's security scheme and the `ingress` field.

**The rules live in World and the relay until a release installs them.**
`world/circle.py` holds the capacity rule, the processor route check, and the
readable set at an ingress. The relay holds token verification and the
second listener. A specification release installs `processor_route` with
its Door profile rules: an authorizer pinned to the active operator,
`class` `service`, and transition-only admission.

## Consequences

- A bandmate or a friend signs in once with an emailed PIN or a passkey and
  uses all three applications from any browser. No one is added to the
  tailnet, and Archive keeps no password.
- The operator can use the applications from a borrowed browser. There they
  are a member, without raw SPARQL or transitions. Governing stays a tailnet
  act, so a stolen Access session cannot approve, withdraw, retire, or
  destroy anything.
- Every byte members read passes through Cloudflare in plaintext, and the
  archive says so. Destroy names Cloudflare as a possible survivor, and a
  route can be ended at any time.
- What a person labelled `AMBER` cannot reach an internet member. The
  application's "circle" setting writes `AMBER` (ADR 0085), so as decided
  today most of what the operator shares with the band is withheld online.
  Question 1 below has to be answered before this is useful.
- The relay makes an outbound HTTPS request to the team domain for keys.
  If that fails and the keys are not cached, the internet listener refuses
  everyone and the tailnet is unaffected.
- RS256 verification needs RSA, which Python's standard library does not
  provide (Open for the operator, 7).

## Relation to other decisions

- [ADR 0016](0016-disclosure.md): Cloudflare is a processor of class
  service. A standing route records the disclosure, and destroy reports it.
- [ADR 0056](0056-tailnet-relay.md): amended. A second, internet listener
  sits beside the tailnet one, and the tailnet rules are unchanged.
- [ADR 0060](0060-relay-interface-and-role.md): `caller` gains `ingress`,
  and one refusal code is added.
- [ADR 0088](0088-bandmates.md): amended. Capacity depends on the ingress as
  well as the person, and the operator's person can be a member over the
  internet (Open for the operator, 2).
- [ADR 0085](0085-documents-lyrics-rights.md): `may_show` gains the ingress;
  sensitivity is checked at service over the internet.
- [ADR 0054](0054-door-service-for-applications.md) and
  [ADR 0063](0063-relay-read-speed.md): unchanged. Door stays on its local
  socket, and both listeners share one relay, its cache, and its write lock.
- [ADR 0093](0093-audiences.md) (audiences) and
  [ADR 0094](0094-peoples-writes.md) (people's writes and grants), proposed
  alongside this one, decide what an internet member may read and propose.

## Alternatives considered

- **Add friends to the tailnet.** W1.1 rules it out. It would also put
  friends on the network the operator's machines share.
- **Tailscale Funnel.** ADR 0056 rejects it. It publishes a port with no
  login in front.
- **One listener, telling the ingress by which header is present.** Then a
  `Tailscale-User-Login` header that passes through the tunnel would make
  the sender the operator. A listener per ingress means no header can change
  the ingress.
- **Trust the tunnel and skip verification.** Any local process, or a path
  that bypasses Access, could then name anyone. Verification is cheap, and
  W1.1 asks for it.
- **Archive as identity provider.** W1.1 rules it out. Archive would hold
  credentials and run account recovery.
- **One processor approval per disclosed revision**, a publication approval
  to the processor covering exactly what each audience approval covers. It
  needs no new contract but doubles every operator act. An approval of class
  service would also apply ADR 0085's rights check, which governs showing,
  and would refuse a bought tablature that only passes through.
- **Operator capacity over the internet with a second factor.** W1.3 rules it
  out. Governing over a provider that sees plaintext would also make that
  provider part of every transition.

## New nouns

**Ingress** (`tailnet` or `internet`).

1. It records which way a request reached the relay, and so whether
   operator capacity is possible. Nothing else records that.
2. It owns no verb. It qualifies the relay's *serve*.
3. It enforces W1.3, and with the route it gives INV-25 something to check
   at the relay.
4. It is code inside an existing concept: a field of `Caller` and the
   listener that sets it. It needs a name because clients see it in the
   `archive` block.
5. It owns no authoritative state. It is configuration and a per-request
   fact.
6. It is a detail of the relay adapter, not a subsystem.

**Processor route** (`archive.transition/processor_route`).

1. It records, before any content is sent, that a destination is reached
   through a processor. Without it there is no record of what Cloudflare
   received.
2. It owns no new verb. It is Publication's *disclose*, toward a processor.
3. It enforces INV-25 and S-32: the relay serves the internet only while a
   route holds.
4. It is already a record inside an existing concept, a transition Claim
   like a publication approval. It needs a contract because S-32 requires a
   recorded disclosure, and configuration is not evidence.
5. It owns authoritative state only as admitted records, through Door.
6. It is a contract and a projection, not a subsystem.

## Touch points

- `relay/relay.py`: `Config` and `load_config` (the `internet` block and a
  second loopback port), `make_handler` and `Handler._agent` (one handler per
  ingress; token verification on the internet one), `Handler.log_message`
  (agent only, never email), `Relay.archive` (`caller.ingress`), and the
  server start around `ThreadingHTTPServer(...)` and `main` (two servers).
- `relay/served.py`: `Served.caller` (capacity from ingress and person),
  `Served.access`, `Served.media_gate` and the media, practice, and export
  routes (the ingress-aware readable set).
- `relay/access.py`: `Caller` (`ingress`), `check_config` (internet
  `logins`), `Access.readable` and `Access.hears`.
- `world/circle.py`: `check_circle_config` and `capacity` (ingress;
  operator's person as an internet member), `readable_set` and `readable_by`
  (service check over the internet), and a new route check.
- `world/documents.py`: `may_show` and `Reach` (the ingress).
- `relay/refusals.py`: `RELAY_CODES` (`processor_not_granted`).
- `relay/openapi.json` (`caller` schema, the security scheme, the code),
  `relay/README.md` (Configuration, Routes), and the relay command line in
  `docs/boundaries.md`.
- `relay/tests/test_relay.py` and `relay/tests/test_circle.py`: forged
  `Tailscale-User-Login` on the internet port, bad `alg`, `aud`, `iss`,
  `exp`, unknown `kid`, the operator's email as a member, and no route.
- `spec/archive-spec.md` in a later release: `processor_route` version 1.

## Open for the operator

1. **`AMBER` cannot pass through a service processor.** ADR 0085 maps the
   application's "circle" setting to `AMBER`, whose furthest class is
   circle. W1.2 makes Cloudflare service, and INV-26 lets only a limit's
   author lift it. So as decided, everything set to "circle" is withheld
   from every internet member. *Recommended:* amend ADR 0085 so the
   application's "circle" setting writes `GREEN` from now on (circle and its
   processors). Existing `AMBER` limits stay tailnet-only until their
   authors relabel them; W1.7 (ADR 0094) lets members do that themselves.
   Do not treat Cloudflare as circle to get around this.
2. **The operator's internet login has no readable set.** ADR 0088 and
   `check_circle_config` say "the operator is never a circle member", and a
   member's readable set excludes the operator's own claims unless they are
   disclosed. *Recommended:* the operator's internet login reads what the
   audiences it is listed in read (ADR 0093), plus its own records that
   those pin. Amend ADR 0088's clause for the internet ingress only.
3. **Capacity is not recorded on claims.** A claim the operator makes over
   the internet names the operator's person, as a tailnet one does. W1.4's
   tiers (ADR 0082 views) will rank it as the operator's, and member rules
   let it supersede the operator's own records of member contracts.
   *Recommended:* accept this for now. It is the same person, and the
   tailnet can supersede back. If a stolen session matters more, a later
   record can add the ingress to the proposal manifest. [ADR 0095](0095-participation.md) (its
   question 2) and [ADR 0098](0098-content-lineage.md) (its question 5) depend on this answer.
4. **The route's policy revision.** S-32 requires a policy revision, and no
   policy language exists (ADR 0007; compare the open question on circle
   disclosures). *Recommended:* make `policy` optional in version 1, and
   require it in the first release that ships policy.
5. **Service or distribution.** ADR 0016 makes a processor whose terms let
   it keep or train on content distribution. Cloudflare's terms for traffic
   and Access logs have not been checked. *Recommended:* the operator reads
   the current terms before the first route is committed, and records the
   finding in the route's `reason`.
6. **Bandmates' tailnet logins from ADR 0088.** *Recommended:* after this
   record, tailnet `callers` bind only the operator, and members come in over
   the internet only. That keeps one rule per ingress.
7. **RSA for RS256.** *Recommended:* add the `cryptography` package, pinned,
   to the relay only, and record it in `docs/boundaries.md`. Do not
   hand-write RSA verification.
8. **Where the email-to-person binding lives.** It is configuration here,
   like tailnet logins. W1.5 makes grants evidence. *Recommended:* keep the
   binding in configuration and the grants in archive evidence (ADR 0094).
   A binding is how a login is recognised, not what it may do.
