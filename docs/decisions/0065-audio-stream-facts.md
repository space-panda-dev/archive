# 0065. Audio stream facts are machine claims about an Artifact

Status: accepted

An Artifact's payload holds only its content digest and size; media type,
duration, and capture details MUST NOT be payload fields (D-8). Nothing else
in an archive says how long a recording is, how many channels it has, or at
what rate it was sampled. The specification therefore admits sample bounds
without checking them against the audio ("Door does not claim that the bounds
fit the audio bytes without separate admitted duration evidence"), the
migration probed sample rates and kept the result only in its own report, and
a client cannot draw a time axis without downloading the audio.

## Decision

**`archive.claim/audio_stream` version 1 states one audio stream's facts.**
It pins exactly one `artifact` Artifact revision and exactly one `run`
(`archive.claim/derivation_run` version 1, ADR 0064). Its payload is:

- `stream_index`: the stream's zero-based index in its container;
- `codec`: the codec's name as the probing method reports it, 1 to 100
  characters;
- `sample_rate`: a positive integer, in hertz;
- `channel_count`: an integer from 1 to 64;
- `sample_count`: a positive integer, the exact number of sample frames
  decoded at `sample_rate`;
- optional `bits_per_sample`: an integer from 1 to 64, for PCM.

**The count is exact or not claimed.** A method that cannot count every frame
(a container's duration estimate, a bitrate guess) does not propose a stream
claim. The method's identifier says how it counted.

**Facts are claims, not truth.** Two runs may disagree; both claims stay and
neither is current. A file with several audio streams has one claim per
stream.

## Consequences

- Spans can be checked against a stream: a span suggestion pins the stream
  claim it measured against, and Door refuses a span past its
  `sample_count` (ADR 0066).
- World projects duration, rate, and channels per Artifact, so a client can
  lay out a recording before any audio arrives.
- The probe tool lives in `enrich/` and its runs are recorded (ADR 0064).

## Alternatives considered

- **Put the facts on the Artifact.** D-8 keeps Artifacts to bytes, so a
  wrong probe could never be corrected or contested.
- **Duration in seconds.** Bounds are integer samples (ADR 0003); seconds
  would need rounding on every comparison.
