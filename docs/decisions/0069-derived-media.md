# 0069. Waveform peaks and listening copies are derived media, served by digest

Status: accepted

A review client drags edges on a waveform and plays recordings in a browser.
Today both come from the V2 system: a "waveform" of one loudness value per ten
seconds, and AAC streams on a separate port. The relay serves no audio
(ADR 0056). Peaks every ten milliseconds or so are needed to place an edge,
and a browser cannot play a two-hour WAV.

## Decision

**Peaks and listening copies are regenerable, not evidence.** They are made
from an Artifact's content by a fixed method and can be made again from it.
They are never admitted. They live under a configured derived-media root at
`sha256/<ab>/<cd>/<digest>/<name>`, where `<digest>` is the source content's
SHA-256 and `<name>` carries the method version. Losing the root loses
nothing (see the [state model](../state-model.md)).

**Peaks are an audiowaveform binary file, version 2.** Each pixel holds a
signed 8-bit minimum and maximum for every channel of the source, with
`samples_per_pixel` equal to the source's sample rate divided by 100 and
rounded half to even, so each pixel is about ten milliseconds. A client
reduces them for wider views.

**A listening copy is AAC-LC in MP4** at 192 kb/s with its index at the
start, downmixed to stereo when the source has more than two channels.

**`enrich/` makes both** from a file. Rerunning on an existing, complete
output writes nothing.

**The relay serves them by digest.** `GET /media/{digest}/peaks` and
`GET /media/{digest}/listen` answer from the derived-media root, with byte
ranges, an entity tag naming the digest and method version, and immutable
caching. The relay answers only for a digest that is an Artifact's content in
World's selected generation, only to configured callers, and never serves an
original's bytes. A missing derivative is a refusal with its own code. This
amends ADR 0056's "serves no content bytes" to "serves no original content".

**Disclosure stays with the operator.** A derivative carries the recording,
so a media route is disclosure if anyone but the operator calls it
(ADR 0016). Until a disclosure policy covers other callers, the relay serves
media only to logins bound to the active operator's person.

## Consequences

- A client draws a waveform and plays audio from the same relay that answers
  its queries, keyed by the digest World already projects.
- Media for a recording appears once `enrich/` has run on it; the importer
  runs it after admission (ADR 0070).
- At about 100 pixels a second, peaks for a two-hour stereo recording are
  about 3 MB before compression.

## Alternatives considered

- **Peaks as claims in World.** Millions of values per recording as triples,
  and a regenerable picture would become permanent evidence.
- **Peaks inside the span suggestions.** It ties the waveform to one
  detector's settings.
- **A second media server.** Another process, port, and caller list to keep
  equal to the relay's.
