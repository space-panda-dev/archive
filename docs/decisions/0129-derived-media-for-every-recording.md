# 0129. Every admitted recording gets its derived media

Status: accepted (operator, 2026-11-27; amends [ADR 0069](0069-derived-media.md)'s
consequences and [ADR 0112](0112-bulk-import-at-scale.md)'s backfill). The
backfill's `--follow` part is accepted but not built.

[ADR 0069](0069-derived-media.md) makes waveform peaks and a listening copy
regenerable derived media, made by `enrich/` and served by the relay by
content digest. Its consequences say "media for a recording appears once
`enrich/` has run on it; the importer runs it after admission (ADR 0070)".
Only `capture import` runs it. Recordings admitted another way, by the V2
migration (ADR 0035) or by a hand admission, never get derived media, and the
relay refuses them with `media_not_derived` for as long as they exist.

On the semantic shadow on 2026-11-27, 94 current Artifacts had an audio stream
claim; 77 had a listening copy and 36 had peaks. A browser that opens one of
the other 17 shows a player that cannot load: the relay answered 404 for
their `listen` route 12 times. Nothing in the archive would ever make them.

## Decision

**Every current Artifact with an audio stream claim should have every kind
of derived media**, whatever admitted it. Derived media stay regenerable,
never admitted, and are made unattended: making them is derivation, which
needs no approval (AGENTS.md, governance grows with irreversibility). Content
a destroy transition names is never derived (ADR 0024).

**A media backfill makes what is missing.** `enrich/media_backfill.py`
reads an `archive export`, finds those recordings, and runs `media.derive`
on each whose content is present, writing only into the derived-media root.
`--dry-run` reports the recordings, what each lacks, and an estimate of the
audio to encode and the bytes to write, and writes nothing. A rerun on
complete outputs writes nothing. It is built with this record, because it is
mechanism under ADR 0069 and makes nothing a person could not already make
with `enrich/media.py` one file at a time.

**Accepted, not built: the enrichment backfill keeps them made.** With
`--follow`, `enrich backfill` (ADR 0112) would make a newly admitted
recording's peaks with its fast tier, and its listening copy in the
background tier, through the same `media.derive`, so a recording becomes
playable as it becomes usable. That changes the running service's work and
its CPU and disk budget, so it is built and deployed on its own.

**The relay does not derive on request.** A missing derivative stays a
refusal with its own code (`media_not_derived`), and a client degrades on
it: it says the listening copy has not been made and offers what the relay
can serve instead (the recording's configured bank stems, ADR 0089).

## Consequences

- One operator command makes the shadow's missing listening copies; at
  192 kb/s a listening copy is about 87 MB an hour of audio.
- The relay's `archive_media` gauge ([ADR 0118](0118-relay-metrics.md))
  shows `missing` falling to zero after a backfill.
- A recording whose content is not present on the machine running the
  backfill is reported and skipped; it is derived where its content is.

## Alternatives considered

- **Derive on the relay's first request, as practice renders are
  (ADR 0089).** A first play would wait minutes for an encode, the relay would
  read originals it otherwise never opens, and its one-render-at-a-time queue
  would be shared with practice audio. The relay serves; `enrich/` makes.
- **Derived media as extractors in the feature bank.** That is an
  [open question](../open-questions.md) (should peaks and listening copies
  become extractors); this record does not answer it.
- **Only the importer, as now.** Leaves every migrated recording without a
  player, permanently.
