# 0104. Transcripts come from local ASR runs, and speaker spans name consenting people

Status: proposed (wave 2, drafted 2026-10-19; open questions settled 2026-10-19)

Talk is half of what a rehearsal holds that the music does not: "take it
from the bridge", "that was the one", a count, an argument about the ending.
Voice notes ([ADR 0103](0103-voice-capture.md), proposed) are nothing but
talk. The applications want to read it, search it ([ADR 0102](0102-retrieval-index.md),
proposed, W2.9), and know who said it.

- [ADR 0085](0085-documents-lyrics-rights.md) already settles what a
  transcript is: a plain-text document Artifact, one line per segment, whose
  `document_source` pins the run and is `derived_from` the recording, and a
  `timed_lyrics` claim asserted by the run that places its lines and words on
  that recording. A transcript is lyrics only when a person says so. Its
  amendment of 2026-10-19 adds that a machine's timing of a *lyric* document
  comes from forced alignment, "never from ASR text". Nothing produces a
  transcript: the V2 transcription unit was not ported, no extractor runs a
  speech model, and no proposer turns one's output into that batch.
- Nothing says who is speaking. The only voice measure is `structure`'s
  `talk` span class ([ADR 0091](0091-talk-needs-level.md)), which says that
  someone talks, not who.
- Naming a voice is identifying a person from their body. A voiceprint is
  biometric data, and a recording holds the voices of people who never chose
  to be in an archive: a friend dropping by, a sound engineer, a child in
  the next room. [ADR 0018](0018-people-in-the-archive.md) lets the people a
  recording records limit its disclosure, but nothing governs whether the
  archive may recognise them.
- [ADR 0016](0016-disclosure.md) makes sending a recording to a hosted
  transcription service a disclosure that policy must grant. The operator
  wants none of that built now.

The operator decided the following on 2026-10-19 (wave 2 decisions,
earlier settled decisions and W2.8); this record states them as given.

- **ASR runs locally only** (hosted ASR stays possible through processor
  policy, not built). **Model weights:** non-commercial or unlicensed
  weights run operator-local only and never ship.
- **Voice capture** includes talk spans in recorder takes and voice notes,
  each voice note its own Artifact (ADR 0103 owns capture; this record
  transcribes what it admits).
- **W2.8 Speakers.** The operator's voice is identified from an enrolled
  voiceprint by default. Anyone else is identified only by their own consent
  claim, made through their login. A voiceprint is biometric: a bank
  feature, regenerable from enrollment audio, never served. Speaker spans
  are machine claims the operator can judge; an unrecognised voice is
  "unknown speaker".
- **W2.9** Transcript text search lives in ADR 0102's disposable index
  store; this record says only what the index reads.

The operator settled this record's open questions on 2026-10-19 (round 6
answer R6.11; R6.16 settles the rest as recommended). The decision below
states the settled choices; the list at the end records them.

## Decision

### Transcripts: an ASR extractor, and ADR 0085's batch

**An `asr-whisper` extractor** ([ADR 0074](0074-extractor-contract.md))
runs a speech model in an environment of its own, as `stems` does, with its
weights pinned by digest and its packages by version (ADR 0074's amendment).
It accepts `audio/*` and needs no feature, so it can travel through Spool
([ADR 0090](0090-extractor-runs-travel-through-spool.md)) to the operator's
own workers. Its parameters name the model, the language (a BCP 47 tag, or
`auto`), and the decoding settings; its outputs are two files: `transcript`
(`text/plain`, UTF-8, one line per segment, as ADR 0085 asks) and `timing`
(`application/json`: for each line its span in samples at the stream's rate
and its words' spans, with the model's confidences as decimal text). The
repository's catalog ships it only with weights whose licence permits
redistribution with AGPL code (the Whisper family's weights are MIT); a
different model is a different extractor id.

**A `transcript` proposer** turns one bank entry into exactly ADR 0085's
batch, admitted together (ADR 0064):

1. the transcript as a document Artifact, its bytes staged by the adapter
   as an import stages content (`adapter.Staging`);
2. `archive.claim/document_source` version 1: `kinds` `["transcript"]`,
   `media_type` `text/plain`, its `run`, and `derived_from` the recording;
3. `archive.claim/timed_lyrics` version 1, asserted by the run, `scheme`
   `text`, `alignment.method` `asr`, its lines and words from `timing`, a
   `confidence_scale` naming the model's measure, and no
   `max_error_samples` until one is measured;
4. [ADR 0098](0098-content-lineage.md)'s lineage claim, since Archive's own
   tools that admit a derived file must propose one: the transcript made
   from the recording, with recipe `{"bank": <citation of asr-whisper/transcript>}`,
   whose digest is the transcript's;
5. the `archive.claim/derivation_run`, `used` the recording, its parameters
   Artifact holding the extractor's key and parameters.

The amendment's "never from ASR text" is read as a rule about lyric
documents: the run's own `timed_lyrics` places the run's own words, which
the original decision provides (Settled 1). A transcript of a song stays a
transcript; it becomes lyrics only by a person's `kinds` or Work timing, and
a lyric document is still aligned by forced alignment, never copied from
ASR output.

**What gets transcribed is policy, how is configuration.** ASR is a run by
request, like every extractor; policy says which content wants it
("transcribe accepted rehearsal audio", the design principle's own example;
"transcribe voice notes"), and configuration says which model on which
machine. Version 1 transcribes the whole content; a view filters lines by
`talk` spans where an application wants talk only (Settled 11). Reach needs
nothing new: the transcript reaches no further than the recording it is
`derived_from` (ADR 0085), and both lineage and `document_source` make it a
destroy dependent of that recording (ADR 0098).

**Weights that may not ship stay on the operator's machines.** An extractor
whose weights are non-commercial, unlicensed, or of unknown licence lives in
an **operator-local catalog**: a directory named in the operator's
configuration, outside the repository, which `bank.Catalog.load` reads
beside the shipped one. Its manifest states the weights' licence in
`requires`, as text for the operator (ADR 0074). The runner refuses to
dispatch such an extractor through Spool to any worker but the operator's
own (`remote.traveling`), since a circle spool would carry the model's work
to someone else's machine, and the repository never names, fetches, or pins
its weights. Its runs and claims are like any other's: the run's software
and parameters say which model made them (Settled 8).

**Hosted ASR is a processor, not built.** Sending audio to a hosted model
would be a disclosure to a service destination that policy grants, recorded
before the send (ADR 0016, INV-25). Nothing in this record sends audio
anywhere; a later record that builds it adds the destination and a policy
rule, and changes no contract here.

### Voices: a bank feature, never served

**A `voice` extractor** accepts `audio/*` and writes three outputs on a grid
of its own: `speech` (spans where a voice is active), `turns` (spans of one
voice, each with a voice number local to this content: diarization within
one recording), and `embedding` (a float series, one speaker-embedding
vector per row). Like `asr-whisper` it needs no feature and runs in its own
environment with pinned weights; if no embedding model with shippable
weights and training data is found, it lives in the operator-local catalog
(Settled 8).

**A voiceprint is the `voice/embedding` rows of an enrollment sample, over
that sample's spans** (W2.8): a bank feature, regenerable from the
enrollment audio, which is evidence. The speaker proposer pools the rows in
memory when it matches and stores the pooled vector nowhere. Three rules
keep it biometric data held for one purpose:

- **Never served.** No relay route serves a `voice` output: the practice
  route's check (`exports.practice_refusal`) refuses the extractor whatever
  the configuration names, and any later route that serves bank files
  inherits that refusal. World projects no bank output, so no SPARQL query
  reaches one.
- **Never indexed.** ADR 0102's index holds no voice embedding; its timbre
  model measures samples, not people. "Where does this person speak" is
  answered from speaker span claims, never by similarity to a voice.
- **Never compared across recordings except to a live consent.** Rows of
  one recording are clustered only within it (`turns`), and compared with
  another recording's rows only as a voiceprint of a person whose consent is
  live (below). An unknown voice is never followed from one recording to
  another, so the archive builds no profile of anyone who did not consent.

Speaker runs stay on the operator's machines: `voice` and the speaker
proposer are never dispatched to a spool that anyone else can lease from,
even a circle one, because the job's payload would carry enrollment audio to
that machine; ASR may go to the operator's own workers only (Settled 7).

### `archive.claim/voice_consent` version 1

A **voice consent** is a person's own claim that the archive may recognise
their voice, naming the audio it may learn it from. It is W2.8's consent
claim; the operator's own is the operator's enrollment. References:

- exactly one `person`, a person Entity revision; the envelope `agent` must
  be that person (Door checks it), so nobody consents for anybody else;
- `sample`: with `decision` `consent`, 1 to 32 distinct revisions, each an
  Artifact or an `archive.claim/audio_selection` Claim revision (version 2,
  or a later installed version) whose spans hold that person's voice alone;
  with `withdraw`, none.

Payload: `decision` (`consent` or `withdraw`) and optional `reason` (1 to
2,000 characters).

```json
{
  "agent": "urn:uuid:…(Ana Reyes, a bandmate)",
  "by": "pkg:generic/review-app@0.9.0",
  "payload": {"decision": "consent"},
  "refs": [
    {"role": "person", "id": "urn:uuid:…(Ana Reyes)", "revision_id": "sha256:…"},
    {"role": "sample", "id": "urn:uuid:…(a selection of her count-in)", "revision_id": "sha256:…"},
    {"role": "sample", "id": "urn:uuid:…(a voice note she admitted)", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

**Latest wins, as for a limit.** For each person, their latest voice consent
by Door's recording time is their **live** answer, ADR 0085's rule for a
person's latest sensitivity claim. A later `consent` replaces the samples; a
`withdraw` ends recognition. A claim is never superseded or retired to
undo it, so the history is the audit trail.

**Made through their login.** The relay accepts a voice consent only from
the person it names, in member capacity through their own login
([ADR 0092](0092-internet-exposure.md)) or, for the operator, in either
capacity; it refuses one naming anyone else as `consent_not_own` (403, a new
code under [ADR 0058](0058-door-refusal-codes.md)), including from the
operator over the tailnet. Unlike a co-writer's sharing consent
([ADR 0088](0088-bandmates.md)), the operator never records one on a
person's behalf, and no operator tool has a command to. Door can check the
agent but not the login, because capacity is not recorded on records
(ADR 0092); the rest is the operator's honesty, as ADR 0018 already says of
limits (Settled 2).

**No grant gates it** ([ADR 0094](0094-peoples-writes.md)): like a member's
own limit or a withdrawn judgment of their own claim, it is the person's own
say, and a `deny` cannot take it away. Its pins must be readable to the
caller, the relay's existing rule. A person enrolls from a selection of
their own voice in a recording they can read, which needs `cut` (allowed by
default), or from a voice note they record, which needs `admit` (denied by
default, ADR 0103).

**The operator by default.** The operator's own voice consent, with samples,
is enough: no other step is needed for the operator to be recognised, and a
`withdraw` from the operator turns it off. Policy does not decide it,
because whether a person is recognised is that person's say.

### `archive.claim/speaker_spans` version 1

A **speaker spans** claim is one run's answer to "who is speaking, when" in
one recording. It is timed (a `sample_rate` and one `stream`), so
[ADR 0081](0081-judgments-and-annotations.md)'s judgment version 2 judges it
by span with no new judgment version. References:

- exactly one `source`, the audio Artifact revision whose samples it counts,
  and exactly one `stream`, an `archive.claim/audio_stream` version 1
  revision about it;
- exactly one `run` (W2.8: speaker spans are machine claims; the agent is
  the tool's Package URL);
- 0 to 16 `speaker`, distinct person Entity revisions, and exactly as many
  `consent`, in the same order: each the voice consent revision whose
  samples gave that speaker's voiceprint.

```json
{
  "sample_rate": 48000,
  "confidence_scale": "cosine similarity of the turn's pooled embedding to the voiceprint",
  "spans": [
    {"start_sample": 96000, "end_sample": 336000, "speaker": 1, "confidence": "0.81"},
    {"start_sample": 312000, "end_sample": 408000, "unknown": 1, "confidence": "0.42"},
    {"start_sample": 960000, "end_sample": 1104000, "unknown": 2, "confidence": "0.30"}
  ]
}
```

- `spans`: 1 to 20,000, in order of start; they may overlap (two people at
  once). Each has `start_sample` and `end_sample` (`0 <= start < end`, ending
  within the stream's `sample_count`) and exactly one of `speaker` (an
  index from 1 into the `speaker` references) or `unknown` (a voice number
  from 1, local to this claim: "unknown speaker 2" here says nothing about
  any other recording).
- `confidence_scale` and `confidence` as in `timed_lyrics`: present on every
  span exactly when the scale is, comparing only within the claim (INV-8).

**Door rules** (a specification release adds them with the bundle): one
Artifact `source` and a `stream` about it; the `sample_rate` equal to the
stream's and spans within its `sample_count`; spans ordered by start;
`speaker` indices in range and every speaker named by some span; `consent`
the same length as `speaker`, each a voice consent version 1 revision with
`decision` `consent` whose `person` is the speaker at its index and which is
that person's live consent in the state the claim commits against, so Door
refuses a claim naming someone whose latest answer is `withdraw`; the agent
a Package URL with a `run`.

**The `speakers` proposer.** It reads the recording's `voice` entry and, for
each person with a live consent, that consent's samples' `voice/embedding`
rows over their spans; pools a voiceprint per person in memory; assigns each
`turns` voice whose pooled rows pass the threshold in its parameters to the
nearest voiceprint, and every other voice to `unknown` with its local number.
Its run uses the recording, each consent revision, and each sample; its
parameters hold the threshold and the bank keys read. Rerun after a new
consent, it proposes a new claim; old claims are kept (INV-7).

**Judging.** Anyone with `annotate` may judge a speaker spans claim, whole
or by span, and every judgment is kept and shown. For the views below, a
span naming a person is **set aside** while the active operator's standing
on it is `rejected` (W2.8), or while the named person's own standing on it
is `rejected`: "that is not me" only narrows what the archive says about
them, so like their limit it needs no grant (Settled 3). Anyone else's
rejection sets nothing aside.

**Only machines assert speaker spans** in version 1 (W2.8): a person who
wants to say "Ana said this" writes a span annotation `note`
([ADR 0081](0081-judgments-and-annotations.md)) (Settled 12). A view may
suggest a session's participants from speaker spans, but no participation
claim is proposed from them (ADR 0095) (Settled 10).

**A span naming a person selects for their own limits (R6.11).** ADR 0094's
records-them test gains a clause, for limits only: the `source` Artifact of
a head speaker spans claim with a span naming the caller, not set aside,
records the caller, under R5.3's condition (the operator asserted or
affirmed the claim, or it arrived in the same admission as the content). A
limit only narrows (INV-26); being named in talk makes nobody a participant.
A machine claim never arrives with its content, so in practice the
operator's affirmation is what makes a span select; see Still open 1.

### World and the relay

- **Projection.** World projects a voice consent as a claim about its
  person, and a speaker spans claim's spans as resources under `arch:`
  terms, each with its samples and either `arch:speaker` (the person) or an
  `arch:unknown_speaker` number scoped to the claim. No voiceprint or
  embedding is projected.
- **`archive.view/speakers` version 1**: for a recording, each speaker span
  of its claims with its run and confidence, naming the person only while
  that person's consent is live at the snapshot and the span is not set
  aside; otherwise the span is shown as "unknown speaker". A withdrawn
  person's earlier spans stay admitted and are shown unnamed, never dropped
  (Settled 4).
- **`archive.view/transcript` version 1**: a transcript's lines and words
  joined from the document only for a viewer who may read it, as
  `lyric_view` joins lyrics, each line with the speaker the `speakers` view
  gives for most of its span. The joined text is a view, never stored.
- **`archive.view/voice_consent` version 1**: for a person (members ask only
  about themselves), their live answer, its samples, and the recordings
  whose speaker spans name them, so a client can offer "recognise my voice"
  and "stop".
- **The index** (ADR 0102) reads transcript text through the `transcript`
  view and returns object IDs that the relay filters by the caller's
  readable set (W2.7); speaker names come from the `speakers` view, filtered
  the same way.
- **Relay.** It accepts voice consents only from the person they name,
  ungated; accepts judgments of speaker spans under `annotate`, and a named
  person's rejection of a span naming them without one (both ungated acts
  join ADR 0094's list of writes no grant gates, in its one wave-2
  amendment, R6.10); serves speaker spans and
  transcripts within reach, as everything; and serves no `voice` output.

## Consequences

- Every admitted recording and voice note can have a searchable transcript,
  made on the operator's machines, reaching no further than its recording.
- The operator is named in talk from their first enrollment. Everyone else
  stays "unknown speaker" until they sign in and say otherwise, and goes
  back to unnamed in every view the moment they withdraw.
- The bank holds speaker-embedding rows of every voice in every recording
  it measured, consenting or not. They are local, regenerable, never served,
  never indexed, and never compared across recordings except to a live
  voiceprint; deleting them loses nothing but the time to remeasure
  (Settled 6).
- A speaker spans claim records which consent each name relied on, so a
  later reader can tell a name from a withdrawn consent from a live one.
- Two new contracts, three views, two extractors, two proposers, and one
  relay refusal code. No invariant changes: INV-7 holds because withdrawn
  and rejected names are kept and shown unnamed; INV-4 because voiceprints
  are bank state; INV-25 because nothing leaves the operator's machines.
- Tests need voices, and INV-23 forbids real ones: fixtures use synthetic
  signals or speech generated by a model whose licence permits it.

## Alternatives considered

- **Speakers on the transcript's lines** (a field of `timed_lyrics`). The
  contract is accepted for 0.0.10, speakers come from audio alone, and a
  recording with no transcript still has voices.
- **Speakers as contributions at span scope.** A contribution says who did
  what to the music ([ADR 0078](0078-contributions.md)); talking is not a
  role, and contributions carry no consent rule.
- **Asserting a participation from speaker spans.** ADR 0095 allows a
  machine participation; talking at a session is not playing in it, and a
  view can suggest participants from speaker spans without a claim
  (Settled 10).
- **Voiceprints as admitted evidence.** They would be biometric data in
  every copy and backup, kept forever (INV-1). As bank features they are
  regenerable and delete with the bank.
- **Clustering unknown voices across recordings.** It would build a profile
  of someone who never consented, which W2.8 forbids by naming only
  consenting people.
- **Operator-entered consent for people without a login**, as ADR 0088 lets
  the operator record a co-writer's sharing consent. W2.8 requires the
  person's own login.
- **A sensitivity limit as consent.** ADR 0018 rejected a separate consent
  mechanism for disclosure; recognition is not disclosure, and a limit
  cannot say "recognise me".

## New nouns

- **Voice consent.** (1) It is the only record of a person's own answer to
  whether the archive may recognise their voice, and from which audio.
  (2) No verb; it is asserted by its person. (3) It lets speaker spans keep
  W2.8's rule that only consenting people are named, beside INV-7 for
  withdrawals. (4) It is code inside an existing concept: a claim contract,
  read by Door's speaker spans rule and World's views. (5) Its claims are
  evidence; whether one is live is computed. (6) A claim contract, not a
  subsystem.
- **Speaker span** (a span of a speaker spans claim). (1) It says who is
  speaking over some samples, which no claim says. (2) No verb. (3) None by
  itself. (4) A field of a claim contract. (5) The claim is evidence.
  (6) A detail of the contract.
- **Voiceprint.** (1) It is the embedding of one person's voice that
  speaker recognition compares with. (2) No verb; the speaker proposer
  pools it. (3) It is the object of the never-served rule. (4) It is bank
  rows read over an enrollment sample's spans, not a store of its own.
  (5) No: regenerable (INV-4). (6) A detail of a feature.
- **Unknown speaker** is a value of a speaker span, and **operator-local
  catalog** an extractor directory from configuration (ADR 0074's catalog
  read from one more place); neither is a new noun.

## Touch points

- `enrich/extractors/asr-whisper/` and `enrich/extractors/voice/` (new),
  each with `extractor.json`; `docs/feature-bank.md`'s shipped extractors.
- `enrich/proposers.py`: `PROPOSERS` gains `transcript` and `speakers`
  (new `propose_transcript`, `propose_speakers`); the forced-alignment note
  above `propose_timed_lyrics` stays as it is.
- `enrich/propose.py`: `propose_artifact` and `run` give the speaker
  proposer the live consents (from the `voice_consent` view) and their
  samples' bank entries.
- `enrich/adapter.py`: `Staging` and `propose`, for the transcript
  Artifact in the same batch as its claims and run.
- `enrich/bank.py`: `Catalog.load` given the operator-local directory from
  configuration; `enrich/remote.py`: `traveling` refuses operator-local and
  speaker extractors except to the operator's own workers.
- `world/documents.py`: `check_document_source` and `check_timed_lyrics`,
  unchanged, used by the proposer; `lyric_view`, reused by the transcript
  view; new `world/speakers.py` with `check_voice_consent`,
  `check_speaker_spans`, and the live-consent rule.
- `world/judgments.py`: `standing`, read for the operator and the named
  person.
- `world/circle.py`: `MEMBER_CONTRACTS` and `check_member_draft` (voice
  consent own-only and ungated; a named person's rejection ungated);
  `world/grants.py` (new, ADR 0094): the records-them test's speaker-span
  clause (R6.11).
- `world/exports.py`: `practice_refusal` refuses every `voice` output.
- `world/record_views.py` and `world/views.py`: `VIEWS` gains `speakers`,
  `transcript`, `voice_consent`; `world/contract_graphs.py` and
  `world/projection/6/vocabulary.ttl`: the two graphs and their terms.
- `relay/access.py` and `relay/refusals.py`: `consent_not_own`.
- `door/src/Archive/Door/PinnedRules.hs` and `SpanRules.hs`: the speaker
  spans rules (consent per speaker, live at commit; spans in the stream);
  `AgentRules.hs`: a voice consent's agent is its person.
- `spec/archive-spec.md` and `spec/contracts/`: the two bundles in a
  release; `docs/vocabulary.md`: *voice consent*, *speaker span*,
  *voiceprint*.

## Settled by the operator (2026-10-19)

1. **ADR 0085's "never from ASR text".** It governs lyric documents only; an
   ASR run's `timed_lyrics` over its own transcript stands (R6.16).
2. **"Made through their login" cannot be proven by a record.** Accepted as
   an honesty limit, as ADR 0018 does for limits (R6.16).
3. **Who may set a name aside.** The operator and the named person, without
   a grant; anyone else's rejection is shown only (R6.16).
4. **After a withdrawal.** Earlier spans stay admitted and are shown
   unnamed; erasure stays the information-erasure question (R6.16).
5. **Unknown voices numbered within one claim.** Yes, never linked across
   recordings (R6.16).
6. **Embedding rows of non-consenting voices.** Kept in the bank: local,
   regenerable, never served or indexed (R6.16).
7. **Speaker runs stay on the operator's machines,** even from a circle
   spool; ASR goes to the operator's own workers only (R6.16).
8. **The operator-local catalog** for weights that may not ship, with no
   manifest version change (R6.16).
9. **A speaker span selects content for the named person's limits.** Yes,
   amending ADR 0094's test, under R5.3's condition (R6.11).
10. **Machine participation from speaker spans.** No claim; a view may
    suggest participants (R6.16).
11. **Whole recordings or talk spans only.** Whole content, filtered by
    `talk` in views; policy chooses which content (R6.16).
12. **Can a person assert speaker spans?** Not in version 1; a span
    annotation `note` covers it (R6.16).

## Still open

1. **What meets R5.3's condition for a speaker span.** Speaker spans are
   machine claims and never arrive in the same admission as their content,
   so under R6.11's condition a span selects only once the operator affirms
   the claim (or the span). *Recommend:* accept that literally; a person who
   is named but not yet affirmed can still limit the content through their
   participation or a part credit they assert themselves (R4.3, R5.2), so
   nothing is lost, and the operator's affirmation keeps a machine's guess
   from selecting.
