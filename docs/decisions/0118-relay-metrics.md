# 0118. The relay answers aggregate metrics to a local probe

Status: accepted; amends [ADR 0114](0114-relay-local-health.md)'s local
probe, which this extends from `/health` to `/metrics`

## Context

The fleet's Archive pane wants to see the archive's shape over time: how
many records of each contract, which specification the archive is at, how
far the media and feature-bank backfills have gone, how much of the archive
has stems, practice renders, lead sheets and work matches, how large each
store is, and how quickly the relay answers. The fleet collects metrics with
Grafana Alloy on each host and asks projects to expose them on localhost
without learning the backend (fleet ADR 0007), labelled by the subjects they
are about and otherwise by bounded dimensions (fleet ADR 0015).

Two places could answer. A read-only `archive metrics` command, run by a
timer into the collector's text-file directory, sees records and stores but
not requests: request latency exists only inside the long-running relay. The
relay already holds World open in its query workers, knows the derived-media
root, the feature bank and the World store, and is the one archive process
the operator keeps running.

## Decision

**`GET /metrics` on the relay, in the Prometheus text format (version
0.0.4).** It answers the local probe of ADR 0114 (a loopback peer with no
`Tailscale-*`, `Origin`, `X-Forwarded-For` or `Forwarded` header) and the
operator over the tailnet; a circle member is refused `operator_only`, and a
forwarded request without an allowed login `forbidden_identity`, as every
other route.

**Only aggregates leave.** Every series is a count, a size, a duration or an
information series whose labels are drawn from fixed or code-defined sets:
record shapes, contract identifiers, derivation methods, extractor ids,
derived-media kinds, coverage features, store names and classes, and
described routes. No series names an archive identifier, an object, a
revision, a content digest, a person, a title or a path, so reaching the
route through a proxy discloses the archive's size and the code it runs,
never its content. The specification an archive is at is named; it is a
version of this public repository's contract.

**The series.**

| Series | Type | Labels | Meaning |
|---|---|---|---|
| `archive_relay_info` | gauge | `role`, `writes`, `commit`, `build` | 1; the relay's configuration and code (ADR 0114) |
| `archive_specification_info` | gauge | `spec` | 1; the specification the manifest names now |
| `archive_relay_request_duration_seconds` | histogram | `route`, `outcome` | time to answer, by described route (`other` for anything else) and `ok`, `refused` (4xx) or `failed` (5xx) |
| `archive_records` | gauge | `shape` | record revisions in World's selected generation |
| `archive_records_by_contract` | gauge | `contract` | record revisions citing each contract, any version |
| `archive_derivation_runs` | gauge | `method` | derivation runs recorded in the archive, by method id |
| `archive_bank_entries` | gauge | `extractor`, `outcome` | feature-bank entries `made`, and outstanding failure records `failed` |
| `archive_bank_entries_outdated` | gauge | `extractor` | entries made by a version other than the installed one |
| `archive_bank_requests` | gauge | | bank requests waiting for the scheduler (ADR 0112) |
| `archive_media` | gauge | `kind`, `state` | audio Artifacts with each derived-media kind `derived` or `missing` (ADR 0069) |
| `archive_coverage` | gauge | `feature`, `state` | `covered` or `uncovered`: audio Artifacts with `stems` and with a `practice_render`, musical Works with a `lead_sheet`, song occurrences with a `work_match` (versions 1 and 2 name the occurrence they compared; a version 3 window match names none and covers no occurrence) |
| `archive_store_bytes` | gauge | `store`, `class` | bytes of regular files in `records`, `content` (annexed objects held here), `world`, `media`, `bank`, `staging`, by state-model class |
| `archive_metrics_collected_timestamp_seconds` | gauge | | when the counts above were collected |
| `archive_metrics_collection_seconds` | gauge | | how long that collection took |

An **audio Artifact** here is an Artifact an audio stream claim describes
whose content no destroy transition names; a **stem separator** is an
installed extractor that declares an audio file output. Record counts come
from World's selected generation, so they are as fresh as World.

**Collected in the background, answered at once.** Counting walks the
feature bank, the derived-media root and the stores, which takes longer than
a scrape should wait. The first request starts a collector thread that
counts again every five minutes; each answer gives the latest counts and
when they were made, and the relay's own series. What the relay is not
configured with (no derived-media root, no bank) has no series rather than
a zero, and what cannot be read is logged and left out of that collection;
the relay never guesses.

**No subject labels from the archive.** Fleet ADR 0015's subjects (`unit`,
`repo`, ...) are the fleet's: these series are all about the one archive the
relay serves, `host` is added by the collector, and the fleet's scrape adds
the relay's `unit`. Every label here is a dimension.

## Consequences

- Alloy scrapes `http://127.0.0.1:PORT/metrics` with no credentials.
- Series stay bounded: about fifty contracts, twenty extractors, and the
  latency histogram, which is most of them: seven buckets plus `+Inf`,
  `_sum` and `_count` for each described route (twenty, and `other`) and
  outcome seen, at most 630 series and in practice the few routes used.
- `relay/openapi.json` describes the route; its 200 answer is
  `text/plain; version=0.0.4`.
- The relay gains no dependency: the text format is written by hand.

## Alternatives considered

- **An `archive metrics` command into a text-file directory.** No request
  latency, a timer to install, and a second process opening World; kept as
  the fallback for an archive with no relay.
- **OTLP push from the relay.** The relay would need an OpenTelemetry SDK, a
  new dependency, for what a pull of plain text gives.
- **Counting on each request.** A bank walk inside a scrape would overrun
  the collector's timeout on a large archive.
- **Per-recording or per-Work series.** Unbounded, and they would name the
  archive's content.
