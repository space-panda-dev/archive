# 0108. Live analysis is Enrichment over a stream, and extractors gain a streaming mode

Status: accepted (operator, 2026-10-19); proposed in wave 3 and its open
questions settled the same day; extends [ADR 0074](0074-extractor-contract.md)

## Context

[ADR 0107](0107-multi-device-and-live.md) settled that live is advice and
batch is evidence: whatever a rehearsal node computes during a rehearsal (a
song guess, a tempo, a tuning hint) is Enrichment over a stream not yet
admitted, is never admitted, and the admitted recording is re-analysed in
batch. [ADR 0106](0106-set-lists-and-clocks.md) lets live song ID use a set
list as a prior and says a live tempo hint is not a clock claim. Neither says
where the live code lives or how it relates to the code that makes the batch
evidence.

Today every measure is an extractor ([ADR 0074](0074-extractor-contract.md)):
one request naming one content by path and digest, one output directory,
checked and filed in the feature bank under a key that includes the content
digest ([ADR 0073](0073-feature-bank.md)). A stream has no digest and no end,
so nothing in that contract can run live. Written separately, a live tempo or
pitch tracker would be a second method that drifts from the batch one, and a
live guess and a later batch claim that disagree could not say whether the
music or the code differed.

The operator decided the following on 2026-10-19 (wave 3 decisions W3.1,
W3.2 and W3.5); this record states them as given.

- **W3.1** (the half this record covers): live analysis code lives in this
  repository's `enrich/` (live analysis is Enrichment); the rehearsal node is
  a separate application repository like the review app (device I/O, outbox,
  UI, phone link).
- **W3.2**: extend ADR 0074's extractor contract with a streaming mode
  (frames in, events out, same manifest) so a live guess and the batch claim
  come from one implementation.
- **W3.5**: live practice feedback (pitch, timing against a lead sheet) runs
  in the browser from the device mic, no upload; advice only; kept takes per
  ADR 0107.

## Decision

### Streaming mode is part of the extractor contract

**A manifest may declare `stream`.** The manifest contract becomes
`archive-extractor/2`: version 1 plus one optional field. Version 1
manifests stay valid, and the batch runner reads both. The manifest is not in
the key, so no entry is re-keyed by the contract change.

```json
"stream": {"command": ["${python}", "frames.py", "--stream"],
           "input": {"sample_rate": 22050, "channels": 1, "format": "f32le"},
           "outputs": {"hpcp": "exact", "rms_db": "exact"},
           "latency_samples": 4096}
```

- `command`: as the batch `command` (ADR 0074's `${name}` and file words).
  It may be the batch program with a flag.
- `input`: the sample frames the program reads (W3.2's "frames": one sample
  per channel, not the `frames` extractor's analysis frames): an integer
  rate, 1 or 2 channels, and `f32le` or `s16le`.
- `outputs`: the declared outputs it streams, each with its **agreement**
  (below). A streamed output keeps its batch kind, unit, columns and grid; a
  `file` output cannot stream.
- `latency_samples`: the most input, at `input`'s rate, the program holds
  before it emits what that input decides; a statement for the node's UI,
  checked by the replay test.

The id, version and `parameters` are the batch ones: one manifest, one
version, one method. In this version only an extractor with no `needs`
streams, as only one with no needs travels through Spool
([ADR 0090](0090-extractor-runs-travel-through-spool.md)) (Settled 2).

**A stream request, audio in, events out.** The streaming runner writes
`stream-request.json` (`archive-extractor-stream-request/1`): the extractor's
id and version, its canonical parameters and their digest, `input`, the
block size it will write, and `events`, the path of a named pipe the runner
made, and optionally `clock`, the path of a second named pipe of clock
events (below). The program reads raw sample frames from standard input until
end of file, and writes **stream events** (W3.2's "events") to `events` as JSON
Lines, one object per line:

```json
{"output": "hpcp", "row": 17, "values": [0.12, 0.0, 0.31, ...]}
{"output": "beats", "item": {"sample": 52311}}
{"output": "tuning_cents", "estimate": -7.0, "through_sample": 441000}
{"end": true, "software": [{"name": "essentia", "version": "2.1b6.dev1438"}]}
```

A stream event carries one row of a series (its row index on the declared
grid), one item of an `events` or `spans` output exactly as the batch output
holds it, or a scalar's running estimate with the input sample it is based
on. After end of file the program writes `end` with its software and exits 0.
Standard output and standard error are logs, as in batch mode, because ADR
0074 rejected results on standard output (they mix with library chatter). The
process-group, timeout and signal rules of ADR 0074 apply unchanged. The
runner checks each line as it checks a batch result: a declared output, the
right width, finite values, a row or sample never earlier than one already
emitted for that output; a bad line ends the stream as a failure.

**Clock events are a second, optional input (R7.3).** When the node keeps
a clock ([ADR 0110](0110-shared-clock.md)), it hands the live runner a named
pipe of its clock events (each tempo, meter, start and stop, at an input
sample position), and the live runner names that pipe as `clock` in the
stream request of a program that declares it reads one. Only `provisional`
outputs and live tools may read the clock; an `exact` output never does,
because a batch run has no clock to read.

**Streaming is deterministic.** Given the same samples, clock events, block
size and parameters, a program emits the same stream events. That is what lets the
replay test below compare them with the batch entry, and makes any log of
them regenerable (INV-4).

### Agreement with batch: how a live answer is compared with the claim

**Each streamed output declares its agreement.** `exact`: the stream
events of the whole input, collected, equal the batch entry's output for the
same samples, bit for bit, whatever the block size. `provisional`: the
events are running guesses (a key or tempo before the end of the take, beats
a batch tracker would revise with hindsight); nothing is promised about
their agreement, only their determinism. A running `estimate` of a scalar is
always provisional.

**One implementation means batch mode runs the streaming core.** An extractor
with `exact` outputs gets them by making its batch program decode the content
to `input`'s format and push it through the same core in blocks (`frames`
already reads ffmpeg's output block by block, `decoded_blocks`). The
streaming runner converts the node's sample frames to `input` with the same
decode arguments the extractor's batch path uses, so the core sees the same
samples. ffmpeg is still not pinned (ADR 0073), so "exact" holds for one
ffmpeg build, as batch entries do.

**The replay test holds a manifest to its agreement.** For every shipped
extractor that declares `stream`, a test in this repository runs the batch
runner on a test recording, then replays the same file through streaming
mode at several block sizes, and asserts each `exact` output equals the
entry's and that no event comes later than `latency_samples` after the input
that decides it. A browser build (below) is held to the same test.

**Comparing one rehearsal.** The node may keep a log of the stream events it
received, beside its recording, as operational state. After the node's part
is admitted and its batch entries exist, `enrich/live.py compare <log>
--content <part> --bank <dir>` reports, per output, where the live events
and the entry made by the same id, version and parameter digest agree and
where they do not. The batch entry is found by the key the installed
extractor gives for the admitted content (ADR 0073), so a log from another
version or other parameters is refused, never compared. Machine claims are
made from that entry by the existing proposers, with their derivation runs
([ADR 0064](0064-derivation-runs.md)); the report is regenerable and never
admitted.

**What a stream event may never be.** It is never admitted, never a claim,
never a derivation run or cited by one (`used` or parameters), and never
filed as a bank entry, even when `exact`: an entry is made by the batch
runner from admitted content, under a key with its digest, and the runner's
checks (Settled 3). It reaches the archive only when the operator acts on it,
as the operator's own claim in the node's outbox (ADR 0107, W2.13); that
claim and the later batch claim coexist (INV-7).

### The live runner in `enrich/`

**`enrich/live.py` is the streaming runner (W3.1).** Standard library only,
like `enrich/extract.py`, whose catalog, command resolution and process
handling it reuses. It runs a fixed set of streaming extractors over one
stream on one machine, and live tools that read their stream events: the
live song guess (the window matcher's method over streamed `frames/hpcp`,
against reference profiles of Works that the node fetched through a relay
read before the rehearsal and hands it as a file (R7.1,
[ADR 0109](0109-rehearsal-node.md)), optionally with a set list as its
prior, ADR 0106), and tempo and tuning
hints read against a set list entry's targets. It proposes nothing, reads no
archive during the rehearsal, writes no bank entry and keeps no state of its
own; ADR 0074's line holds: no workflow engine, no scheduler, no retries.

**The node talks to it through a process, not a library.** The node
application (its own repository, W3.1,
[ADR 0109](0109-rehearsal-node.md)) starts `enrich/live.py run` as a
child for the rehearsal, writes the sample frames it records to its
standard input, and reads `archive-live-event/1` lines from its standard
output: each stream event with the extractor, version and parameter digest
that made it, and every sample position also stated in the node's own
recording samples (rounded half-even), which is what an outbox response
anchors to. A process boundary keeps this repository free of a library shared
with a sibling project and of a daemon or event bus
([left out on purpose](../boundaries.md#left-out-on-purpose)): one producer,
one consumer, for the life of a rehearsal.

**Its events on standard output are allowed.** This record keeps stream
events off an extractor's standard output (below, and ADR 0074) because
libraries print there. The live runner is not an extractor: it is standard
library only, loads no analysis library into its own process, and prints
nothing on standard output but `archive-live-event/1` lines; its extractors'
standard output and standard error are its logs, never its standard output.

**The node pins what it runs (R7.2).** The node pins the live runner by a
tagged release of this repository, and each streaming extractor by its id,
version and code digest, as ADR 0074 pins an extractor's code; the live
runner refuses to start an extractor whose installed manifest does not
match the pin it is given, and every live event names the extractor, version
and parameter digest that made it.

**A stream follows the node's parts.** The node feeds the live runner
exactly the samples it writes to its recording, losslessly, and starts a new
stream at each part rollover, because each part is its own Artifact with its
own batch entries. Stream events and entries therefore line up part for
part. Live tools may keep their own state across parts (a song guess need
not forget the song at a rollover).

**Spool carries no streams.** A Spool task is a leased job with a result,
not a stream; streaming runs on the node's machine only.

### Browser practice feedback (W3.5)

**It is advice in a client, not an extractor run.** Pitch and timing
feedback against a lead sheet ([ADR 0084](0084-lead-sheets.md)) runs in the
practice client from the device's microphone; no audio leaves the device;
nothing is admitted; its state is application state
([ADR 0089](0089-practice-audio-and-exports.md)). A take the person keeps is
admitted per ADR 0107 (W2.14) and analysed in batch like any Artifact; the
feedback the client showed is not kept with it.

**Unless it reuses an extractor compiled for the browser.** A browser build
of a streaming extractor (to WebAssembly, say) counts as that extractor, under
its id and version, only if it is built from the same source and passes the
replay test against the batch entry. Then its feedback can be said to agree
with the batch measure of the kept take; otherwise it is the client's own
code and says nothing about any extractor. A browser build ships code, and
any model, to every device that opens the client, so only an extractor whose
code and weights may be distributed can have one; weights licensed for
non-commercial or local use never ship (Settled 4).

### Which accepted records this extends

- ADR 0074: `stream` in the manifest (`archive-extractor/2`), the stream
  request and stream events, agreement, the replay test.
- ADR 0073: unchanged; streaming never writes the bank.
- ADR 0006 and INV-9: a streaming program reads a copy of the samples and
  changes nothing; output worth keeping still enters through Door, and here
  only as a person's claim.
- ADR 0107: the node's live computation is named (`enrich/live.py`) and its
  samples are the recording's; outbox and responses are unchanged (ADR 0109
  publishes the outbox format and starts the live runner; ADR 0110 feeds it
  the clock).
- ADR 0106: live song ID's prior is a set list, as there; the batch matcher's
  prior rule is unchanged.
- [Boundaries](../boundaries.md): `enrich/` gains a tool that reads a stream
  rather than a file, and a command.

## Consequences

- A live tempo, tuning or pitch hint and the batch claim on the same part
  come from one manifest and one version, and for `exact` outputs from the
  same bits; where they disagree, the comparison says whether the cause is a
  provisional guess or the music.
- Making an existing extractor stream changes its files, so its pinned code
  digest changes (`test_a_shipped_extractor_changes_only_with_its_version`).
  Where the replay test shows the batch outputs unchanged, the new digest is
  recorded; otherwise it is a new version, and for `frames` that re-keys
  every entry in the bank below it (Settled 1).
- Restarting at each part rollover costs a provisional output its context for
  the first seconds of a part; batch has the same boundary.
- Live tools in `enrich/` run on the node's machine, so that machine needs
  this repository's checkout and the extractors' environments, as the archive
  machine does.
- No invariant changes. Nothing is built by this record.

## Alternatives considered

- **Live code in the node's repository.** A second implementation of each
  measure, drifting from the batch one; W3.1 places it in `enrich/`.
- **A separate live manifest or extractor id.** Two versions to keep in step,
  and no way to say a live answer and a claim are one method; W3.2 says same
  manifest.
- **Stream events on standard output.** ADR 0074 rejected standard output for
  results; libraries print there.
- **Filing an `exact` stream as the bank entry after admission.** It would
  save one decode, but entries would then come from two writers, one of which
  never saw the admitted bytes.
- **Uploading practice audio for server-side feedback.** W3.5 says no upload.

## New nouns

- **Streaming mode.** (1) It runs an extractor over an unbounded stream,
  which the batch contract cannot. (2) It owns no verb; it is a way to
  *extract*. (3) It serves INV-9 and keeps live advice out of the archive
  (ADR 0107). (4) It is code inside an existing concept: a field and a
  protocol of the extractor contract. (5) No. (6) A protocol.
- **Stream event.** (1) One line of streamed output. (2) None. (3) None of
  its own; it is never admitted. (4) A message format of the streaming
  protocol; "event" here is broader than the `events` feature kind, which one
  stream event may carry, and the vocabulary should say so. (5) No.
  (6) A protocol detail.
- **Agreement** (`exact`, `provisional`). (1) It says what a streamed output
  promises against batch. (2) None. (3) None; the replay test checks it.
  (4) A manifest field. (5) No. (6) A detail.
- **Live runner** (`enrich/live.py`). (1) It runs streaming extractors and
  live tools for the node. (2) None beyond *extract*. (3) It proposes
  nothing, so INV-2 holds by construction. (4) It is an enrichment tool, as
  `extract.py` is. (5) No. (6) An operation.
- **Browser build.** A build of an extractor for a client, under the same id
  and version, held to the replay test; no state, verb or invariant.

## Touch points

- `enrich/bank.py`: `check_manifest` and `Extractor` (the `stream` field,
  `archive-extractor/2`); `check_events`, `check_spans` and
  `check_output_declaration`, reused for stream events.
- `enrich/extract.py`: `resolve_command`, `run_program` and the catalog,
  reused by the live runner.
- `enrich/live.py` (new): `run` (with the clock pipe) and `compare`; `enrich/test_live.py` (new):
  the replay test.
- `enrich/extractors/frames/frames.py`: `decoded_blocks` and
  `decode_arguments`, the first streaming core if `frames` streams first.
- `enrich/test_bank.py`: `SHIPPED`, the digests of any extractor that gains
  `stream`.
- `docs/feature-bank.md` (manifest, request and a streaming section),
  `docs/adding-an-extractor.md` (a step for streaming), `enrich/README.md`.
- `docs/boundaries.md`: the `enrich/` paragraph and the commands list
  (`enrich/live.py run | compare`).
- `docs/vocabulary.md`: *streaming mode*, *stream event*, *live runner*.
- Outside this repository: the node application starts `enrich/live.py`;
  the practice client holds any browser build.

## Settled by the operator (2026-10-19)

1. **The first streaming extractors.** `frames` (`hpcp` and `rms_db`
   exact, for the song guess and level) and `rhythm` (beats and tempo,
   provisional). `f0-pesto` (pitch, for practice feedback) streams only after
   its code and weights are checked distributable. Each is added only where
   the replay test shows its batch outputs unchanged, so its digest is
   recorded rather than its version bumped (R7.6).
2. **Streaming an extractor that needs features.** Not in this version;
   allowed when a live hint needs it, with the live runner feeding the
   needed extractor's stream events as the need, and the key rule unchanged
   (R7.12).
3. **An `exact` stream never fills the bank.** Batch runs on the admitted
   content are the only writer of entries (R7.12).
4. **Where a browser build lives.** Its build recipe beside the extractor in
   `enrich/extractors/<id>/`, under the same version, with the replay test
   here; the compiled file is built and served by the practice client's
   repository from a pinned checkout, and only for extractors whose code and
   weights may be distributed (R7.12).
5. **The node keeps its stream-event log** as operational state beside its
   recording, deleted with it; since it is regenerable by replay, `compare`
   may also replay the admitted part instead (R7.12).
6. **The node machine holds the extractors' environments** for the laptop
   node; phone and tablet nodes use browser builds or native code held to
   the replay test, and until then send no live hints (R7.12).
7. **Reference profiles.** The node fetches the Works' reference profiles
   before the rehearsal through a relay read, with the rest of its read side
   (ADR 0109), and hands them to the live runner (R7.1).
8. **Pinning.** Streaming extractors by id, version and digest; the live
   runner by a tagged release (R7.2).
9. **The clock.** An optional second named pipe of clock events in the
   stream request (R7.3, ADR 0110).
