# 0063. Relay reads at interactive speed: cache, concurrency, and gzip

Status: accepted

A browser client reads World through the relay on every load. On 2026-10-15,
at the shadow's size (a few thousand records), the two
large reads of the first client took 1.0 to 1.5 seconds each over the
tailnet, and a small query waited behind them. The relay answered one request
at a time (ADR 0056), kept no answers, and sent JSON uncompressed.

Measured on the archive machine against a copy of the shadow's World store,
for the client's two large reads:

| | records | claims |
|---|---|---|
| query evaluation | 85 ms | 213 ms |
| building results | 30 ms | 37 ms |
| JSON encoding | 12 ms | 16 ms |
| crossing the worker pipe | 7 ms | 11 ms |
| relay over loopback | 165 ms | 316 ms |
| relay over the tailnet, from a laptop | 1,050 to 1,120 ms | 1,230 to 1,460 ms |
| body, plain and gzipped | 1,395 KB and 128 KB | 2,062 KB and 165 KB |

Most of the time is spent sending 1.4 to 2 MB of uncompressed JSON over a
tailnet path with a 60 to 110 ms round trip. On the machine, evaluation
dominates and building results does not, so the relay keeps World's own
result building; Oxigraph's JSON serializer was no faster end to end (92 ms
and 223 ms). The claims read scans every quad to test graph names; reaching
claim graphs through the records graph takes 32 ms instead (ADR 0059, World's
README).

## Decision

**Answers are cached by what they depend on.** The key is the projection
version, the generation that answered, the graph format, and the query text.
A generation never changes once built (ADR 0055) and its name is never reused,
so a cached answer is right for as long as it is kept; a refresh selects a new
generation, which is a new key. The relay reads the version's pointer before
each query to know the generation, which costs one small file read. Answers
are kept as encoded bytes, with the gzipped form once a client has asked for
it, up to `cache_bytes` (default 64 MiB), least recently used first out.
Identical questions that arrive together are answered once. The cache is
regenerable state in the relay's memory (see the
[state model](../state-model.md)); a restart empties it.

**The relay warms the next generation.** A thread checks the pointers each
second and is woken at once after the relay's own commit. When a projection's
generation moves, whoever moved it, the thread answers the 32 most recently
asked questions of that projection again against the new generation and then
drops the old generation's answers. A question the new generation refuses,
such as one now over the row limit, is skipped; its asker gets the refusal.
A failed warming is logged and does not stop the thread.

**Reads run side by side.** The relay serves each connection on its own
thread and keeps a pool of `query_workers` (default 3) query worker
processes, each opening World read-only, which Oxigraph allows. A request
takes an idle worker. Each keeps its time and row limits, and an overrunning
worker is still stopped and replaced. Workers encode their answers, so
encoding runs in parallel and only bytes cross the pipe.

**Writes stay one at a time.** Every Door request takes the relay's write
lock, so Door's serial service sees one request at a time, and a commit's
World refresh finishes before the next write is planned. Reads do not take
the lock: a query never delays a write, and a read during a refresh answers
from the generation selected when it began.

**Answers are gzipped when the client asks.** With `Accept-Encoding` allowing
gzip (RFC 9110), any answer of 1 KiB or more is sent with
`Content-Encoding: gzip`, and every answer carries `Vary: Accept-Encoding`.
Browsers ask by default.

## Consequences

- At shadow size on the archive machine, a cached answer is sent in a few
  milliseconds, and a small query is not held behind a large one. The tests
  assert the costs: a cached answer never reaches a worker, a cached gzip
  body is not compressed again, Door never sees two requests at once, and a
  small query is answered while a two-second one runs.
- Uncached, the client's records read takes about 165 ms on the machine. Its
  claims read, as written, spends 213 ms in evaluation alone; written as
  World's README recommends, it takes about 100 ms.
- The first read after a refresh is warm if the same question was asked of
  the previous generation. A question never asked before is answered at full
  cost once per generation.
- The relay holds up to `cache_bytes` of answers in memory, and runs up to
  `query_workers` processes, each holding the store open.
- ADR 0056's "requests are handled one at a time" now holds for writes only.

## Measured after

On the archive machine over loopback, against a scratch copy of the shadow
(ADR 0061) running this relay, medians of repeated asks:

| read | uncached | cached | cached, gzip |
|---|---|---|---|
| catalogue records, as the client writes it | 116 ms | 2.5 ms | 1.7 ms |
| catalogue claims, as the client writes it | 262 ms | 3.3 ms | 2.1 ms |
| all records | 232 ms | 3.9 ms | 1.8 ms |
| all claims, as written | 282 ms | 5.6 ms | 2.5 ms |
| all claims, as World's README recommends | 96 ms | 6.4 ms | 2.0 ms |
| judgment claims, by the contract link | 31 ms | 2.4 ms | 1.7 ms |

A small query asked while an uncached claims read ran was answered in 5 ms.

From a laptop over the tailnet (62 ms round trip), on one kept-alive
connection as a browser keeps it, the cached large reads took 81 to 97 ms
gzipped against 251 to 539 ms plain, and 1.0 to 1.5 seconds before this
change. A new connection adds two to three round trips for TLS.

The client's catalogue claims read stays above 250 ms uncached because it
scans graph names; the recommended pattern brings it under 100 ms.

## Alternatives considered

- **HTTP caching instead of a relay cache.** A client could revalidate with an
  ETag naming the generation, but every first load would still pay for the
  answer, and other clients would not share it.
- **Precomputed answers per refresh.** Faster for the questions chosen, but
  World would have to know its clients' questions; warming learns them from
  what clients ask.
- **Oxigraph's serializer for SELECT results.** It cannot stop at the row
  bound, which ADR 0059 requires, and measured no faster in total.
- **More workers and no cache.** Concurrency alone keeps small queries quick,
  but every load would still pay the full evaluation and transfer.
