# 0102. World keeps a second disposable store for facets, embeddings and text search

Status: proposed (wave 2, drafted 2026-10-19)

The sample app is a browser over thousands of short sounds: cuts from the
review app, songbook songs, and libraries of kicks and snares
([ADR 0099](0099-samples.md) and [ADR 0100](0100-packs-and-collections.md), both proposed). Its questions are "loops near 92
BPM in A minor", "snares like this one", and, once transcripts exist
([ADR 0104](0104-transcription-and-speakers.md), proposed), "where did we talk about the bridge".
World cannot answer any of them well today.

- World is one Oxigraph store of named RDF graphs (ADR 0041, ADR 0055).
  SPARQL can filter a projected number, but the numbers a sample browser
  filters on are not projected: BPM, key, length and loudness are bank
  features ([ADR 0073](0073-feature-bank.md)), and W2.2 keeps them views,
  never records. SPARQL has no nearest-neighbour search and no full-text
  search; a `regex` over every transcript scans every literal.
- Features are not claims and are never projected (ADR 0073 rejected
  "features as claims"); a timbre embedding is hundreds of numbers per
  sound, which as triples would be the same mistake.
- Members never send SPARQL: `/world/query` is operator-only
  (`OPERATOR_ROUTES` in `world/circle.py`). They read named views, filtered
  by their readable set, which [ADR 0093](0093-audiences.md) now computes per
  audience. A search over everything that answered members would reveal
  what was never disclosed to them, through what it matches, how many hits
  it holds back, or how it ranks.
- [ADR 0059](0059-world-read-contract.md) refuses an answer over the row
  bound and never truncates, and every relay answer names the generation it
  came from ([ADR 0060](0060-relay-interface-and-role.md),
  [ADR 0063](0063-relay-read-speed.md)).

The operator decided the following on 2026-10-19 (wave 2 decisions W2.6,
W2.7 and W2.9, and the earlier settled decision on the index that wave 2
turns into a contract); this record states it as given.

- **The retrieval / similarity index is a second disposable store inside
  World** (World's verb relate/query; not a new noun).
- **W2.6 The first index** holds facets from the bank (BPM, key, duration,
  loudness, form, sound) and one timbre-embedding model with permissive
  weights for "sounds like this". Sample identification by fingerprinting
  gets its own ADR after a spike; it is named here as future work only.
- **W2.7 Index results respect audiences.** The index returns object IDs;
  the relay filters them by the caller's readable set (ADR 0093) before they
  leave, so a hidden item is never revealed. Withheld counts follow INV-24.
- **W2.9 Transcript text search** lives in the same disposable index store
  as W2.6 (World's SPARQL has no full-text search).

## Decision

### What the index is

The **index** is World's second store: regenerable state (INV-4), built only
from a World generation and the feature bank, holding nothing either lacks.
Losing it costs a rebuild. It owns no archive contract, Door never reads or
writes it, and no policy or transition reads an answer from it (ADR 0041,
"queries do not govern"). Something learned from it that is worth keeping,
such as "these two kicks are the same sample", is proposed to Door as an
ordinary claim under its own contract (ADR 0011's promotion).

It holds three kinds of row, each about one **unit**: an `audio_selection`
Claim object (a cut or sample, any installed version), or an Artifact whose
content is audio (a library one-shot, a recording), or a transcript
segment (below).

- **Facets**: one named value about a unit, with the revisions it depends on.
  Machine facets are ADR 0099's `archive.view/sample_facets` answers for
  the unit, computed by that view's rule (bank entries read by the key the
  installed extractors give, ADR 0073, over the unit's spans, then the
  selection's recipe applied), never by a rule of the index's own, so a
  facet in the index always equals that view's answer:

  | Facet | From | Value |
  |---|---|---|
  | `bpm` | the view's `bpm` | number |
  | `key` | the view's `key` | tonic and mode |
  | `duration` | the view's `length`, in seconds | seconds |
  | `loudness` | the view's `loudness` | as the view states it |
  | `form` | W2.1 form claims (ADR 0099) | a concept of the local scheme |
  | `sound` | W2.1 sound claims (ADR 0099) | a MusicBrainz instrument ID |
  | `tag` | W2.1 free-tag claims (ADR 0099) | text, filter only |

  `form`, `sound` and `tag` rows come from claims, people's and the machine
  proposer's (ADR 0100's source paths) alike: one row per claim revision,
  with its asserter and the active operator's standing on it
  (`standing` in `world/judgments.py`). Disagreeing claims each have a row
  (INV-7). A person's "4 bars at 92" (W2.2's meter and key claims) is a
  further `bpm` and `key` row with its claim revision, beside the machine's.
  Tags filter and never rank (W2.1).
- **Embeddings**: one timbre vector per unit, from a new extractor (below),
  for "sounds like this". Vectors never leave the index: no answer carries
  one, and the relay serves no route that returns one.
- **Search text**: the text of each transcript segment (a line of a
  transcript document, placed on its recording by the run's `timed_lyrics`
  claim) as ADR 0104's `transcript` view joins it, with the span of the
  recording it covers, in a full-text table.

**Every row carries its dependencies.** A row names the revisions it was
made from: the unit's revision (the selection's Claim revision, or the
Artifact revision whose content was measured) and, for a claim-derived
facet or a transcript segment, that claim or document revision too. A row
may be answered to a caller only when every revision it names is in the
caller's readable set (below).

### The timbre extractor

One extractor under ADR 0074's contract, `timbre-<model>` version 1, writes
a series of embedding rows on its own grid (a model window, about one
second) for any audio content. It runs by request through Spool like every
extractor (ADR 0090); the index never runs an extractor, it only reads
entries that exist. A unit's embedding is the mean of the series' rows over
its spans, L2-normalised; a whole short Artifact uses every row. The
manifest pins the model files by digest (ADR 0074's amendment) and states
the weights' licence, which must permit redistribution (W2.6). A
transform recipe in a selection (ADR 0099) is not applied to the
embedding (the facets above do apply it, through the view): a selection is
indexed as its source span sounds, and a render that was chosen and
admitted is an Artifact with its own content, indexed as itself.

No speaker or voice embedding enters the index. A voiceprint is biometric
and never served (W2.8, ADR 0104); a "sounds like this voice" search would
be identification by another route.

### Building it: index generations

The index is built in the World store root, beside the projection, by the
engine below. One build is an **index generation**: immutable once built,
installed by one rename, selected by a pointer `INDEX-i<N>` for index
version N, exactly as ADR 0055 installs and selects a projection
generation. Its metadata records the World generation it read (of the
newest projection version), the index version, and every bank entry key it
read. Its name is a digest of those, so a generation never changes and its
name is never reused, which is what the relay's cache needs.

- **When.** `world refresh` builds a new index generation after it selects
  a new projection generation. `world index` (new) builds one when the World
  generation is unchanged but the bank holds entries the selected index
  generation lacks, as when a Spool run brings back `timbre` series for
  yesterday's cuts. Nothing changed means nothing written (INV-5).
- **Incrementally.** As ADR 0055: copy the selected generation and add rows
  for records added since its World snapshot and for units whose bank
  entries have since appeared. Tests compare an incremental generation with
  a full build from the same World generation and bank entries.
- **Destroyed content.** A build never reads a bank entry of content World
  knows to be destroyed, and drops that content's rows. A build keeps the
  selected and the previous index generation and deletes older ones, so a
  destroyed sound's embedding survives at most one generation after the
  refresh that saw the destroy. The bank's own copies remain ADR 0073's
  gap ([open](../open-questions.md#interfaces-and-tooling)). This answers
  the erasure question for the index's copies only; transcript text that is
  evidence stays evidence, and whether it stays searchable is the open
  question's to answer.

**Engine.** SQLite through Python's standard `sqlite3`: plain tables for
facets, an FTS5 table for search text (`unicode61`, diacritics removed, no
stemming, since transcripts are not all in one language), and embeddings as
little-endian `f32` blobs, searched by exact cosine distance over the
allowed rows in the query worker. At ten thousand units of 512 dimensions
that is 20 MB and a few milliseconds; an approximate index is a later
decision when a measurement needs one. No new service, no vector database
(docs/boundaries.md, "Left out on purpose").

**Description.** `world/index/<N>/index.json` publishes, per index version,
the units, the facets with their types and sources, the embedding method
(extractor id and version, pooling), and the search-text sources, as
`world/projection/<N>/` describes a projection. ADR 0059's versioning
applies: adding a facet, unit kind or text source keeps the version;
renaming or removing one, or changing what a facet means, bumps it, with the
90-day overlap. A new embedding model is a new extractor version and keeps
the index version, because no client sees vectors; answers name the method
that ranked them.

### The relay: one search route

`POST /world/search` answers the operator and members alike (it is not in
`OPERATOR_ROUTES`). The request:

```json
{
  "index": 1,
  "units": ["selection", "artifact"],
  "like": {"id": "urn:uuid:…(a readable unit)", "revision_id": "sha256:…"},
  "text": ["bridge", "half time"],
  "facets": {
    "bpm": {"min": 88, "max": 96},
    "key": [{"tonic": "A", "mode": "minor"}],
    "duration": {"max": 8},
    "form": ["loop"],
    "sound": ["<MusicBrainz instrument ID>"],
    "tag": ["dusty"]
  },
  "claims": "unrejected",
  "limit": 50,
  "after": null
}
```

- `index` names the index version, as a query names its projection; an
  unserved version is refused `index_not_served`.
- At most one of `like` (rank by cosine distance to that unit's embedding)
  and `text` (rank by FTS5 relevance; the words are quoted by the relay and
  never passed as FTS5 syntax, as SPARQL `SERVICE` is refused before the
  engine runs). With neither, hits are ordered by unit identifier. `facets`
  filter only. `like` must name a revision the caller can read, or the
  answer is `not_disclosed`; a unit that has no embedding yet is answered
  as `unindexed`, not refused.
- `claims`: `unrejected` (the default) lets a claim-derived facet row match
  unless the active operator's standing on it is `rejected`; `any` lets
  every row match. Machine facets always match.
- `limit` (1 to `max_rows`) and `after` (an opaque cursor from the previous
  answer) page through one ordering. The question is "the first `limit` hits
  in this order", so an answer of `limit` hits with a cursor is complete for
  its question, not truncated (ADR 0059). A `limit` over `max_rows` is
  `request_malformed`.

The answer, inside ADR 0060's envelope:

```json
{
  "snapshot": "<commit>", "index": 1, "index_generation": "<name>",
  "method": {"like": "timbre-<model>@1, mean over spans, cosine"},
  "hits": [
    {"id": "urn:uuid:…", "revision_id": "sha256:…", "unit": "selection",
     "score": 0.91,
     "facets": {"bpm": [{"value": 92.1, "by": "rhythm@4"}],
                "form": [{"value": "loop", "claim": "sha256:…", "standing": "affirmed"}]},
     "text": {"segment": "sha256:…", "span": {"start_sample": 0, "end_sample": 96000, "sample_rate": 48000},
              "snippet": "…the [bridge] in half time…"}}
  ],
  "more": "<cursor or null>",
  "unindexed": 3,
  "withheld": 412
}
```

**Filtering happens before ranking (W2.7).** The relay gives the query
worker the caller's readable set from the selected World generation
(`Access.readable`); the worker restricts every table to rows whose
dependencies are all in it before it filters, ranks or counts. It then
returns object and revision IDs, and the relay checks each again against
the readable set, and each content's media gate (`Access.hears`), before
the answer leaves. Filtering after ranking would let hidden items take
slots in the top `limit`, so the number shown would reveal them. Because
the readable set is read from the selected World generation, never from the
index, an index generation that lags behind a withdrawn approval or a
destroy can only show less.

**What is counted.** `unindexed` counts readable units that passed the
filters but lack a row the ordering needs (no `timbre` entry yet, no
transcript), so an empty answer is never mistaken for "nothing matches"
(INV-24). `withheld` counts indexed units the caller cannot read, over the
whole index generation, independent of the request: it says the answer is
not over everything, as named views' `withheld` does, without saying which
hidden units would have matched. The operator's answers have `withheld` 0.

**Workers, limits and cache.** Search runs in the existing query worker
pool (`QueryPool`), each worker opening the index read-only beside World,
under `query_timeout_seconds` (`query_timeout`). Answers are cached by
index generation, World generation, the digest of the caller's readable
set, and the canonical request; callers with the same readable set share
answers. They are not warmed, because they depend on the caller (as views
today, ADR 0063). New codes, each with one status (ADR 0060's rule):
`index_not_served` (404) and `index_unavailable` (503, no index generation
selected yet). `relay/openapi.json` describes the route.

### Abilities

None of [ADR 0094](0094-peoples-writes.md)'s abilities applies: searching is
reading, bounded by the readable set like every read. A member who finds a
loop and cuts from it uses `cut`, and one who tags it uses `annotate`, as
today. Searching by a sound that is not in the archive (uploading an example)
would need staged content and is not offered; see Open for the operator.

### Fingerprinting is later

Identifying a sample's source by audio fingerprint gets its own record after
a spike (W2.6). It would be another bank extractor and, when kept, a claim;
the index may later hold its codes for lookup under the rules above.

## Consequences

- The sample app filters by tempo, key, length, loudness, form and sound,
  ranks by timbre, and searches transcripts, in one request each, for the
  operator and for every member over what their audiences read.
- A member learns nothing about undisclosed items from search: not their
  facets, not whether they match, not how many of them match. They learn the
  size of what is withheld, as every view already tells them.
- Facet values equal the W2.2 views' by construction and by test; the index
  stores no fact the archive or the bank does not hold (INV-10, INV-4).
- A cut becomes findable by timbre only after a `timbre` run for its content
  and the next `world index`; until then it counts as `unindexed`.
- World's store root grows by the index: about 2 KB a unit for embeddings,
  plus transcript text. Each refresh adds an index build proportional to
  what was added.
- The bank becomes an input of a World read for the first time. A lost bank
  empties the index's machine facets and embeddings until extractors run
  again; it never makes an answer wrong, only `unindexed`.
- No invariant changes. INV-24 holds through `unindexed` and `withheld`;
  INV-18 holds because filtering reads the narrowest readable set and a
  missing row never matches.

## Alternatives considered

- **Project facets and embeddings into RDF.** Embeddings as triples repeat
  the mistake ADR 0073 rejected, and SPARQL still has no vector or text
  search.
- **A SPARQL text extension or an engine with one.** It would tie clients
  to an engine's dialect, and the readable-set filter would have to be
  written into each query; members do not send SPARQL.
- **A separate search service** (a vector database, Elasticsearch-style
  engines). A daemon and a store to operate for a few thousand units; SQLite
  in the standard library holds them, and the index stays a file World can
  delete.
- **Filtering after ranking**, the literal reading of "the relay filters
  them". It reveals hidden items through missing slots; filtering first and
  checking again on the way out meets W2.7 in both readings.
- **A per-request withheld count.** It would say how many hidden items match
  a word or a sound, which W2.7 forbids.
- **Index rows per member or per audience.** Readable sets change with every
  approval; one index filtered at read time needs no rebuild when they do.
- **Embeddings served to clients** for client-side search. They are
  regenerable features of content; serving them adds a disclosure surface
  for no need the sample app has.

## New nouns

The index itself is not a noun: it is World's second store, under World's
verbs relate and query (the operator's decision), and *index* is already
INV-4's word for regenerable state.

- **Facet.** (1) It names one filterable value of a unit with the revisions
  it came from; nothing else gathers machine views and claimed values under
  one filter. (2) No verb; World's *query* reads it. (3) None of its own; its
  dependency list is how the relay keeps INV-24 and ADR 0093's readable set.
  (4) It is code inside World: a row of the index. (5) No; regenerable.
  (6) A detail of the index.
- **Embedding.** (1) A vector measured from content that places similar
  sounds near each other; no other feature is meant for distance. (2) No
  verb; an extractor writes the series (ADR 0074), the index pools it.
  (3) None. (4) It is a bank series and an index column. (5) No.
  (6) A detail. It is not a claim and is never served.
- **Index generation.** (1) It fixes which World generation and bank entries
  one build read, so cached answers stay right. (2) No verb. (3) INV-4, by
  being deletable at any time. (4) It is ADR 0055's generation applied to the
  second store. (5) No. (6) A detail.
- **Unit** (of the index) is a word for "what one hit names": a selection
  object, an audio Artifact, or a transcript segment. It is a field value,
  not a concept.

## Touch points

- `world/world.py`: `refresh_store` and `refresh_served` (build the index
  after the projection), `_install_generation` and `_replace_pointer`
  (reused for index generations and `INDEX-i<N>`), the argument parser in
  `main` (a new `index` subcommand).
- `world/index.py` (new): the build, the engine, the dependency check, the
  query given an allowed set; `world/index/1/index.json` (new).
- `world/circle.py`: `readable_by`, unchanged, called by the relay for
  search; `OPERATOR_ROUTES` unchanged (search is not in it).
- `world/judgments.py`: `standing`, read for claim-derived facets.
- `enrich/bank.py`: `key_of`, `Catalog`, and the reader, used by the
  build to find entries by installed key.
- `enrich/extractors/timbre-<model>/` (new) and `docs/feature-bank.md`'s
  shipped extractors table.
- `relay/relay.py`: `ROUTES`, `make_handler` (`_answer`), `QueryPool` and
  `_query_worker` (a search message), `AnswerCache` (keys with a readable-set
  digest), `Config` and `load_config` (`max_rows` and
  `query_timeout_seconds` reused).
- `relay/access.py`: `Access.readable`, `Access.check_disclosed` (the `like`
  unit), `Access.hears` (the media gate on hits).
- `relay/refusals.py`: `RELAY_CODES` gains `index_not_served` and
  `index_unavailable`; `relay/openapi.json` gains the route.
- `docs/state-model.md` (the index in the regenerable list and World's
  loss row), `docs/boundaries.md` (the relay paragraph), `world/README.md`.
- ADR 0099's W2.2 view rule, which the build calls rather than repeats.

## Open for the operator

1. **The engine.** SQLite with FTS5 and exact vector search is proposed;
   the alternative is an embedded vector library. *Recommend:* SQLite and
   exact search now; an approximate index only when a measured search over
   the real library exceeds 100 ms.
2. **The model.** W2.6 asks for one permissive timbre model, not which.
   *Recommend:* a short spike over two or three candidates whose weights'
   licence is verified to permit redistribution, judged by the operator
   listening to the neighbours of twenty library sounds.
3. **Long recordings.** A mean embedding of an hour-long rehearsal means
   nothing. *Recommend:* embed only selections and Artifacts up to 30
   seconds in index version 1; windowed search inside long recordings
   ("where in our rehearsals does this snare sound") is an addition after
   the size is measured.
4. **What `withheld` counts.** INV-24 asks that a withheld result be said;
   W2.7 asks that nothing hidden be revealed. *Recommend:* the
   request-independent count above; a per-request count is refused for good.
5. **Which text is searched.** W2.9 names transcripts. *Recommend:* add
   titles, span annotations and notebook pages
   ([ADR 0105](0105-notebook.md), proposed) as further text sources in later
   additions under the same dependency rule, each a separate index source.
6. **Claimed facets by default.** No current rule exists yet for form and
   sound claims. *Recommend:* `unrejected` as the default until ADR 0099 or
   a later record gives a current rule, then the current rule.
7. **Search by an uploaded example.** *Recommend:* not now; when wanted,
   only for callers with `admit`, through staging, with the example
   discarded after the answer unless admitted.
8. **Destroyed content's transcripts.** The index drops its own copies of
   destroyed content but keeps searching an admitted transcript of it.
   *Recommend:* leave that to the information-erasure question; the index
   follows whatever disposition the transcript is given.
