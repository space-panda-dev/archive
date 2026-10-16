# Relay

The relay carries Door's service and World's query to the operator's browser
over the tailnet ([ADR 0056](../docs/decisions/0056-tailnet-relay.md)). It
listens on the loopback interface only; `tailscale serve` publishes it with
HTTPS and adds the caller's tailnet login. It owns no state.

## Run

The relay needs Python with World's dependency installed
([World](../world/README.md#install)), a running `door serve` for the archive,
and a World store for it:

```sh
door serve --socket /path/to/private-dir/door.sock      # in the archive
relay/relay.py --config /path/to/relay.json
tailscale serve --bg --https=8450 8450
```

`relay.json` is a closed object:

```json
{
  "listen": {"host": "127.0.0.1", "port": 8450},
  "archive_root": "/path/to/archive",
  "door_socket": "/path/to/private-dir/door.sock",
  "world_store": "/path/to/world-store",
  "archive_command": "/path/to/archive",
  "staging": "/path/to/private-staging",
  "writes": "dry-run",
  "role": "shadow",
  "origins": ["https://host.tailnet.ts.net:8449"],
  "callers": {"login@example.com": "urn:uuid:..."},
  "query_timeout_seconds": 10,
  "max_rows": 10000
}
```

`role` is `shadow`, `scratch`, or `live`, and every answer reports it;
clients commit only where it is `scratch`. A `shadow` relay must be `dry-run`
([ADR 0060](../docs/decisions/0060-relay-interface-and-role.md)).
`callers` maps each allowed tailnet login to the object identifier of the
person it acts as. `staging` must be a directory owned by the relay's user and
closed to others. A `commit` relay refreshes World after each commit with
`archive_command`, which needs `ARCHIVE_GIT_EXECUTABLE` and
`ARCHIVE_GIT_ANNEX_EXECUTABLE` in the relay's environment, as Door does.

## Routes

[`openapi.json`](openapi.json) is the description: every route, body, answer,
status, and refusal code, as OpenAPI 3.1 with JSON Schema 2020-12. The tests
validate every answer against it.

Every request must carry an allowed `Tailscale-User-Login`; a request with an
`Origin` must come from a listed origin; bodies are `application/json`, at
most 1 MiB. Answers are `{"ok": true, "archive": ..., "result": ...}` or
`{"ok": false, "error": {"code", "message"}, "archive": ...}`. `archive` names
the archive, its specification, the relay's role, and the World projection
versions served.

| Route | Body | Result |
| --- | --- | --- |
| `GET /health` | | `{healthy, writes, door, world}`; 503 unless Door and World both answer |
| `POST /world/query` | `{"query": sparql, "projection": n}` | `{snapshot, projection, media_type, data}`: SPARQL Results JSON or N-Triples |
| `POST /door/admit-dry-run`, `/door/transition-dry-run` | `{"manifest", "records"}` | `{"door": ...}`, Door's answer |
| `POST /door/admit`, `/door/transition` | the same | `{"door": ..., "world": {"status": "refreshed" \| "stale", ...}}`; refused unless `writes` is `commit` |

A proposal's `manifest` is Door's manifest
([ADR 0023](../docs/decisions/0023-admission-and-staging.md),
[ADR 0028](../docs/decisions/0028-transition-proposals-and-dispatch.md)), and
`records` maps each path it lists, `records/<name>.json`, to that draft.
Admissions must list no `contents`, and every draft's `agent` must be the
caller's. Resending an identical proposal is Door's retry: it returns the
committed revisions and writes nothing. That is also what to do after
`outcome_uncertain`.

## Test

```sh
python -m pip install -r world/requirements-test.txt
ARCHIVE_TEST_DOOR=$(cabal list-bin door) ARCHIVE_TEST_ARCHIVE=$(cabal list-bin archive) \
  python -m unittest discover -s relay/tests
```

Without the two variables, the tests that run Door through the relay are
skipped.
