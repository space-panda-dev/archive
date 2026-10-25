# Relay

The relay carries Door's service and World's query and named views to the
operator's browser over the tailnet
([ADR 0056](../docs/decisions/0056-tailnet-relay.md),
[ADR 0082](../docs/decisions/0082-views-and-current.md)), serves derived media,
stems, and practice renders by content digest
([ADR 0069](../docs/decisions/0069-derived-media.md),
[ADR 0089](../docs/decisions/0089-practice-audio-and-exports.md)), stages
documents for admission and serves admitted ones
([ADR 0085](../docs/decisions/0085-documents-lyrics-rights.md)), renders
exports, and, when it is configured with a circle, serves the operator's
bandmates what was disclosed to them
([ADR 0088](../docs/decisions/0088-bandmates.md)). It listens on the
loopback interface only; `tailscale serve` publishes it with HTTPS and adds
the caller's tailnet login. It owns no archival state: what it keeps (staged
documents, renders, exports) is operational or regenerable.

## Run

The relay needs Python with World's dependency installed
([World](../world/README.md#install)), a running `door serve` for the archive,
and a World store for it. It reads the derived-media layout from
`enrich/derived.py`, so run it from a source tree that holds `enrich/` beside
`relay/` and `world/` (the flake's `tools` package does):

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
  "max_rows": 10000,
  "media_root": "/path/to/derived-media",
  "circle": {"destination": "band relay", "members": ["urn:uuid:..."], "band": "urn:uuid:..."},
  "bank_root": "/path/to/derived-media",
  "practice_features": ["stems/bass", "stems/drums", "stems/other", "stems/vocals"],
  "renderers": {"practice-rubberband": {"version": "1", "command": ["/path/to/renderer"]}},
  "exporters": {"lead-sheet-lilypond": {"version": "1", "command": ["/path/to/exporter"]},
                "lead-sheet-ireal": {"version": "1", "command": ["/path/to/python3", "/path/to/archive/documents/ireal/export.py"]}},
  "render_timeout_seconds": 3600,
  "export_timeout_seconds": 120
}
```

`role` is `shadow`, `scratch`, or `live`, and every answer reports it;
clients commit only where it is `scratch`. A `shadow` relay must be `dry-run`
([ADR 0060](../docs/decisions/0060-relay-interface-and-role.md)).
`callers` maps each allowed tailnet login to the object identifier of the
person it acts as. Without `circle`, every login acts as one person, the
operator. With it, every caller but one is a `members` person, and that one
is the operator; the relay refuses to start unless World names that person
as the active operator and each member as a person Entity. `destination` is
the circle's name exactly as a publication approval names it, and `band`,
optional, is shown to clients and has no other effect. `staging` must be a directory owned by the relay's user and
closed to others. A `commit` relay refreshes World after each commit with
`archive_command`, which needs `ARCHIVE_GIT_EXECUTABLE` and
`ARCHIVE_GIT_ANNEX_EXECUTABLE` in the relay's environment, as Door does.
`media_root` is optional: it names the derived-media root that
[`enrich/media.py`](../enrich/README.md) writes. Without it the relay works as
before, and the media routes refuse with `media_not_derived`. The relay must
run as the user that made the derived media, whose files only that user can
read. `bank_root` is the feature bank's root (often the same directory), and
`practice_features` the `<extractor>/<output>` features it serves, each of
which an installed extractor must declare as an audio file; none by default.
`bank_scheduler` (default false) says a scheduler makes the bank's requests
([ADR 0112](../docs/decisions/0112-bulk-import-at-scale.md)): a served
feature not made is then asked for with a bank request and answered as
queued (`feature_queued`, 202, for its file; `queued` in the listing and in
a practice answer), never as an empty result; without it the answers are
`feature_not_made` and `stems_not_made`. The request is all the relay
writes in the bank.
`renderers` and `exporters` name programs installed on the relay's own host
by an absolute path, each with the version that stands for its code. One
exporter is built: `lead-sheet-ireal` version 1 (`documents/ireal/export.py`,
ADR 0116), which writes one untimed lead sheet as an iReal Pro link, format
`ireal`. No renderer is built; without one configured, practice renders and
exports are refused (`renderer_unknown`, `exporter_unknown`). A renderer is never run on the
seedbox, and never separates stems: it mixes stems the bank already holds.

## Routes

[`openapi.json`](openapi.json) is the description: every route, body, answer,
status, and refusal code, as OpenAPI 3.1 with JSON Schema 2020-12. The tests
validate every answer against it.

Every request must carry an allowed `Tailscale-User-Login`, except a local
probe's `GET /health` (no identity, not forwarded), which is answered
`{ok, healthy, writes, commit, build}` and nothing else
([ADR 0114](../docs/decisions/0114-relay-local-health.md)), and its
`GET /metrics`, the aggregate metrics below; a request with an
`Origin` must come from a listed origin; bodies are `application/json`, at
most 1 MiB. Answers are `{"ok": true, "archive": ..., "result": ...}` or
`{"ok": false, "error": {"code", "message"}, "archive": ...}`. `archive` names
the archive, its specification, the relay's role, and the World projection
versions served.

| Route | Body | Result |
| --- | --- | --- |
| `GET /health` | | `{healthy, writes, door, world, commit, build}`; 503 unless Door and World both answer |
| `GET /metrics` | | the Prometheus text format, operator or local probe only ([ADR 0118](../docs/decisions/0118-relay-metrics.md)) |
| `POST /world/query` | `{"query": sparql, "projection": n}` | `{snapshot, projection, media_type, data}`: SPARQL Results JSON or N-Triples |
| `POST /door/admit-dry-run`, `/door/transition-dry-run` | `{"manifest", "records"}` | `{"door": ...}`, Door's answer |
| `POST /door/admit`, `/door/transition` | the same | `{"door": ..., "world": {"status": "refreshed" \| "stale", ...}}`; refused unless `writes` is `commit` |
| `GET /world/views` | | `{views}`: every named view and version World serves |
| `POST /world/view` | `{"view", "version", "subject", "parameters"?}` | `{snapshot, projection, view, version, subject, parameters, answer, withheld}` |
| `GET /media/{digest}/peaks` | | the waveform peaks, `application/octet-stream` |
| `GET /media/{digest}/listen` | | the listening copy, `audio/mp4` |
| `GET /media/{digest}/bank` | | `{content, features, missing, queued?}`: the configured features made for this content; with a scheduler, `queued` names each one not made with the key its entry will have |
| `GET /media/{digest}/bank/{extractor}/{output}` | | one configured feature's file, such as a stem; with a scheduler, `feature_queued` (202) while it is not made |
| `POST /media/{digest}/practice` | a practice request | `{key, status, media_type, bytes?, speed, semitones}`; `status` is `ready`, `rendering`, or `queued` (the stems are asked for) |
| `GET /media/{digest}/practice/{key}` | | a ready practice render, `audio/flac` |
| `POST /documents/stage` | `{"media_type", "content", "filename"?}`, bytes as base64 | `{digest, size, annex_key, staging_remote, media_type, artifacts}` |
| `GET /documents/{digest}` | | an admitted document's bytes, with its document source's media type |
| `POST /exports` | an export request | `{key, format, media_type, bytes, losses}` |
| `GET /exports/{key}` | | a made export's bytes |

The `archive` block also names `writes` and the `caller`: their `agent` and
`capacity`, `operator` or `member`, and, on a relay with a circle, the
`circle`'s `destination` and `band`. A client commits only where `role` is
`scratch`, or `live` with `writes` `commit` (ADR 0088's amendment of
ADR 0060).

A proposal's `manifest` is Door's manifest
([ADR 0023](../docs/decisions/0023-admission-and-staging.md),
[ADR 0028](../docs/decisions/0028-transition-proposals-and-dispatch.md)), and
`records` maps each path it lists, `records/<name>.json`, to that draft.
An admission may list `contents` only when every row is bytes this relay
staged for the same caller through `/documents/stage`, and each Artifact
carrying them is described in the same batch by a document source claim of
the staged type; anything else is `content_not_relayed` or
`content_not_described`. Every draft's `agent` must be the caller's.
An admission manifest may include `expected_reference_heads` (ADR 0122):
non-null exact head pins for existing explicitly referenced objects. Door checks
them under its write lock; a moved reference refuses a new batch with
`expected_head_stale`. Complete identical retries still write nothing even if
the referenced head later changes. The relay forwards these guards unchanged;
clients must not remove them to work around an older Door's refusal.


### Members

A member reads through named views and the content routes, never raw
SPARQL: `/world/query` and the transition routes are `operator_only`. What a
member may read, the readable set, is computed by World from the circle's
publication approvals, what they pin, and the members' own records, less
whatever a sensitivity label keeps from the circle (`world/circle.py`,
`relay/access.py`); it is read once a World generation. A view is given only
the member's readable set, says how many records it `withheld`, and may name
nothing outside it (`not_disclosed`, whether or not it exists); the
`readable_set`, `reach`, and `may_publish` views are the operator's. A member's admission
drafts must cite member contracts, name the member (or the band, for a lead
sheet they adopted), and pin and supersede only what they may read.
Staging documents is the operator's.

### Practice audio and exports

A practice request names the recording, the stems entry by its key, a gain
per stem, and optionally semitones and speed (`check_practice_request` in
`world/exports.py`). The relay renders at most one file at a time, in the
background, by running the renderer as `command... <input.json>
<output.flac>`; the input holds the request, its key, each stem's path, and
the stems it pitch-shifts (never drums). The render is filed under the
recording's derived media by its key, with the request beside it. A render
that fails is reported once as `render_failed`; the next request tries again.

An export request names exact revisions to render and Artifacts to join
(`check_request` in `world/exports.py`). The relay runs the exporter as
`command... <input.json> <output> <losses.json>`, where the input holds the
request, its key, the records it renders and what they pin (as World reads
them, limited to what the caller may read), and each joined Artifact's
bytes by path; the exporter writes the file and a JSON list of what it
dropped. Exports are filed under `exports/` in the derived-media root and
served only to callers who may read every rendered and joined revision. Resending an identical proposal is Door's retry: it returns the
committed revisions and writes nothing. That is also what to do after
`outcome_uncertain`.

### Media

A media route answers with the derivative's bytes, not JSON; its refusals
are JSON as above. `{digest}` is `sha256:<64 lowercase hex>`, the content
digest World projects in an Artifact's payload. One byte range is honoured
(206, with `Content-Range`); a range starting past the end is
`range_not_satisfiable` (416, with `Content-Range: bytes */<size>`); several
ranges or an unreadable `Range` send the whole file. `ETag` names the digest
and the method version, `If-Range` is honoured with it, and `Cache-Control`
is `private, max-age=31536000, immutable`.

The relay answers only:

- for a digest that is an Artifact's content in World's selected generation
  and that no destroy transition names, else `media_unknown_digest`;
- to the caller bound to the active operator's person, or to a member the
  recording is disclosed to (every Artifact carrying the content in their
  readable set), else `media_forbidden`, because a derivative carries the
  recording and serving anyone else would be an unrecorded disclosure
  (ADR 0016). The same gate serves stems and practice renders (ADR 0089);
- from a regular file at the layout path, else `media_not_derived` when it
  is absent and `internal_error` when it cannot be read or is not a regular
  file (a link is never followed).

How the relay knows the operator: `callers` binds each login to a person's
object identifier, and World projects the operator initialization, whose
`appoints` reference names the person it made operator. Succession is
unsupported (D-7), so that person is the active operator. If World names no
such person, or more than one, the relay serves media to no one. It asks
World once for each generation, through the same query pool and cache as
`/world/query`, for every Artifact's digest, every destroyed digest, and the
appointed person.

## Test

```sh
python -m pip install -r world/requirements-test.txt
ARCHIVE_TEST_DOOR=$(cabal list-bin door) ARCHIVE_TEST_ARCHIVE=$(cabal list-bin archive) \
  python -m unittest discover -s relay/tests
```

Without the two variables, or without `ffmpeg` for the media tests, the
tests that need them are skipped.

## Metrics

`GET /metrics` ([`metrics.py`](metrics.py), [ADR 0118](../docs/decisions/0118-relay-metrics.md))
answers in the Prometheus text format, version 0.0.4, for a collector on
the archive machine (`curl -s http://127.0.0.1:PORT/metrics`) or the
operator. The first request starts a thread that counts every five minutes:
record revisions by shape and contract from World's selected generation,
derivation runs by method, feature-bank entries by extractor (made, failed,
made by another version) and waiting requests, derived media and coverage
(stems, practice renders, lead sheets, work matches), and store sizes by
state-model class. Each answer gives the latest counts, when they were
collected, the relay's role, commit and build, the specification the
manifest names, and request durations by described route and outcome. Every
label comes from a fixed or code-defined set; no series names an archive,
object, digest, person, or path. What is not configured has no series, and
a part that cannot be read is logged (`relay: metrics: ...`) and left out.
