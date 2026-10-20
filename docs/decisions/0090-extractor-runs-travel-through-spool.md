# 0090. An extractor run travels through Spool, and its files come back as result attachments

Status: accepted (operator, 2026-10-19); amended 2026-10-19 (a stem is a
stem: equivalence by a measured, recorded tolerance; the device is
provenance, not identity)

[ADR 0074](0074-extractor-contract.md) made every extractor run describable
without shared memory and named the Spool capability a worker would offer,
`archive-extractor.<id>@<version>`, but left the return path open: Spool's
results were JSON, and an extractor's outputs are files. Stems are where it
matters ([ADR 0075](0075-stems-as-content.md)): the bank lives on the
operator's eight-core server, which separates a recording at about four
CPU-seconds per second of audio, while the operator's Mac has a GPU that
does it several times faster. Spool 0.0.4 now carries **result
attachments** (Spool's ADR 0020): a program leaves files in `outputs/`,
named by their SHA-256, the worker sends each one back under its lease, and
whoever reads the result may `take` them by digest.

## Proposed decision

**A task is one entry.** `enrich/remote.py put` computes the entry's key
exactly as the runner would (the extractor's version, the operator's
parameters, the content's digest) and, unless the bank already holds that
entry, puts one task: its ID is `bank-` and the key's hexadecimal digits, so
putting it again while the spool keeps it is `existing`; its capability is
`archive-extractor.<id>@<version>`; its one attachment is the content; its
payload (`archive-extractor-task/1`) names the extractor, the key, the full
parameters, and the content's digest and media type. Only an extractor that
needs no other feature travels: a need is a file of the bank, which the
task does not carry.

**The worker runs the real runner.** `enrich/remote.py work` is the
capability program. It refuses, with exit 65 so Spool does not try again,
a task its own extractor cannot make: another version, parameters it does
not declare, or a key it does not compute itself. Otherwise it runs the
extractor through `extract.py`'s runner into a bank inside Spool's working
directory, so every check a local run makes is made there, moves each
output's file into `outputs/` under its digest, and prints the entry's
manifest as the result (`archive-extractor-task-result/1`). The working
directory, with the content and the outputs, is deleted by the worker after
the files are sent: a GPU machine with a small disk keeps nothing.

**The bank files only what it can check.** `enrich/remote.py collect` reads
the spool's results and, for each, recomputes the key from the entry,
requires the extractor's version and parameters to be its own and the task's
ID and capability to be the entry's, takes each file by its digest under
the name its own manifest gives it, and runs the runner's own result check
over the directory (every declared file, shapes, finite values, and digests
computed here). The entry is filed only when that check gives exactly what
the worker's entry says, by the same lock and rename as a local run, and
then the task is collected. A refused result files nothing, is recorded
beside where the entry would be, and stays on the spool for a person.

**Where a model runs is a parameter.** The `stems` program accepts
`apply.device` `cpu` (its manifest's) or `mps` (Apple's GPU); the device is
in the parameters, so a GPU's stems have their own key beside the CPU's.
They are not the CPU's bits: on a 94-second recording every stem differed
in 0.1 to 0.2 % of its samples, by one 16-bit step at most, about 90 dB
below the stem. Two runs on one machine gave the same bits.

**Sending content to a worker discloses it.** A task carries the
recording to whichever worker leases it. This decision covers workers in the
operator's custody only; how a disclosure to anyone else's machine is
recorded before the put stays open
([open questions](../open-questions.md#interfaces-and-tooling)).

## Consequences

- No new key, entry, or format: an entry made through Spool is the entry a
  local run makes, and a reader cannot tell them apart but by the software
  it records.
- The spool host's account must be able to read the bank's staging
  directory, and the requester must be able to run that spool's `put`,
  `results`, `take`, and `collect` locally: Spool's remote `put` cannot
  carry attachments.
- A worker's grant needs `max_result_bytes` (Spool 0.0.4) at least as large
  as the outputs it may hold at once; a recording-hour's stems are about a
  gigabyte.
- A stopped worker stops its extractor: Spool signals the program's process
  group, and the runner starts each extractor in a group of its own, so
  `remote.py work` passes the signal on.

## Alternatives considered

- **A worker writing to a shared bank.** Needs a file system both machines
  mount, and a worker able to damage entries it did not make.
- **Bytes in the JSON result.** Base64 grows by a third and meets Spool's
  output limit; Spool rejected it in its ADR 0020.
- **A worker-side shim that runs only the extractor's program.** The runner
  already checks results and writes the manifest; running it on the worker
  keeps one implementation of both.
- **Trusting the worker's entry.** The worker is another machine; the bank
  recomputes every digest and the key, as it would for its own run.

## Amendment, 2026-10-19: a stem is a stem

Status of the amendment: accepted (operator, 2026-10-19) for the principle
below; the thresholds and mechanics the operator did not state are listed
under [For the operator to confirm](#for-the-operator-to-confirm). Made with
[ADR 0112](0112-bulk-import-at-scale.md), which needs it. The text above is
left as it was accepted; where it disagrees with this section, this section
holds.

### Context of the amendment

"Where a model runs is a parameter" filed a GPU's stems under a key of their
own, so a recording separated on the Mac's GPU was, to every reader, a
recording without stems: the melody, pitch and note entries made over the
CPU's stems were not found over the GPU's, and a bank that wanted the GPU's
speed had to give up everything already made on the CPU, or make both.

The backfill profile (2026-10-19; one 554 s and one 2715 s rehearsal, both
copies, run on the x86-64 laptop and the Mac) measured the difference and
what it does downstream:

- **Speed.** Separation is 59% of a backfill's compute. The Mac's GPU
  separates at 0.10 s per audio-second, 5.5 times the laptop's rate (0.55 at
  four threads).
- **Samples.** On a 60 s clip with voice and bass, every GPU stem differs
  from the laptop CPU's by at most one 16-bit step (largest difference
  3.05e-5; signal-to-difference 91 dB on vocals, 81 on bass, 87 on other).
  The Mac's own CPU differs from the laptop's by the same amount, within
  0.7 dB: two CPUs already disagree as much as a CPU and a GPU do.
- **Downstream.** Notes from `notes-basic-pitch` over the GPU's stems score
  note F1 1.000 against the notes over the CPU's (64 vocal notes, 69 bass
  notes), though the files are not byte-identical. `f0-pesto` over the
  vocal stems differs by a median of 0 cents and 0.1 cent at the 99th
  percentile, with every frame's voicing the same. The bass stem was never
  confident enough on that clip for `f0-pesto`, so bass pitch is untested.

### Decision of the amendment

**A stem is a stem.** Several stem entries of one recording may coexist,
made by different devices (and, later, different tools), and any of them
whose agreement with the others is measured within a recorded tolerance
stands in for the others wherever the application needs "the stems" of a
recording: practice audio, melody, pitch, notes, the bass line. Equivalence
is decided by measurement, never by key identity.

**The device is provenance, not identity.** `apply.device` stays a parameter,
so each device's stems keep their own key and entry, and nothing already in
a bank is rekeyed or rerun. What changes is lookup: the key stops being the
only way to find "the stems".

**What changes in the bank (key and lookup).**

- **The key does not change.** `archive-feature-bank-key/1` and every
  existing key stay as they are. Two devices' stems are two entries under
  the recording's directory, each recording its device in its parameters
  and its libraries in `software`; a claim made from either copies them
  into its run (ADR 0064), so provenance says which stem was read.
- **The manifest declares what may stand in.** An extractor's manifest may
  declare `interchangeable`: the parameter fields whose values make
  equivalent entries (for `stems`, `apply.device` with the values `cpu` and
  `mps`, in that order) and the agreement each needs, as downstream
  measures with thresholds (below). Declaring it changes no output, so it
  is not a new version.
- **The equivalence is recorded.** `enrich/extract.py equivalence <extractor>`
  runs the extractor with each declared value over a set of test inputs,
  runs the named downstream measures over each result, and writes an
  **equivalence record** (`archive-feature-bank-equivalence/1`) into the
  bank: the extractor and version, the parameter digests compared, the
  inputs' digests, every measured value (the sample-level differences as
  evidence, the downstream agreement as the decision), the thresholds, the
  software on each side, and pass or fail. It is regenerable bank state
  (INV-4), never admitted.
- **Lookup follows a passing record.** `Bank.entry` for an extractor that
  declares `interchangeable` looks for the configured parameters' key
  first, then, only when the bank holds a passing equivalence record for
  the pair, for each other declared value in the manifest's order, and
  answers the first entry present. Because `feature`, `input_of`,
  `entry_from`, `default_input` and `default_entry` all go through it,
  practice audio, preferred inputs and the proposers follow. A measure of a
  feature (`entry_from`) is found over whichever in-tolerance stem it was
  made over, the stem of the configured device first: a melody made over
  the GPU's stems stays found after the CPU's stems arrive, and nothing is
  measured twice. Without a passing record, lookup is exactly as before:
  the configured key alone (it fails closed).
- **Making counts a stand-in.** The runner and `remote.py put` treat an
  in-tolerance entry as present (`hit`), so a recording separated on the
  Mac is never separated again on the laptop; `--exact` asks for the
  configured key alone.

**The tolerance.** Downstream agreement decides; sample-level differences
are recorded as evidence and not gated, since they say nothing about what a
reader gets. Each record holds, per test input and stem:

| Measure | Threshold | Measured (2026-10-19) |
|---|---|---|
| `notes-basic-pitch` note F1 (onset within 50 ms, same pitch) over each pitched stem | at least 0.98 | 1.000 (vocals, bass) |
| `f0-pesto` absolute difference on frames voiced in both, 99th percentile | at most 5 cents | 0.1 cent (vocals) |
| `f0-pesto` voicing agreement | at least 99% | 100% (vocals) |
| largest sample difference, signal-to-difference per stem | recorded only | one 16-bit step; 65-91 dB |

The set of test inputs must include material on which bass pitch is
confident, which the profile's clip did not.

### Consequences of the amendment

- The Mac's GPU stems serve the application as soon as a passing record is
  in the bank, and bulk import can send all separation there
  ([ADR 0112](0112-bulk-import-at-scale.md)).
- An upgrade of torch or demucs on either machine is a reason to measure
  again: a record names the software it compared, and lookup honours only
  a record whose software matches the entries it would let stand in.
- The open question of a key that holds no software versions
  ([open questions](../open-questions.md#interfaces-and-tooling)) is
  answered for interchangeable parameters by this record; it stays open for
  the rest.
- `f0-pesto` on the GPU would need a `device` parameter, which changes its
  parameters and so every `f0-pesto` key (below).

### Rejected in the amendment

- **Dropping the device from the key.** Two devices' stems would collide on
  one key, so they could not coexist, and an entry's bits would depend on
  which machine wrote first.
- **Equivalence by byte identity.** Two CPUs already differ by one 16-bit
  step; nothing would ever stand in.
- **Equivalence asserted once in the manifest, unmeasured.** A library
  upgrade could move a device's output and nothing would notice.

### For the operator to confirm

1. The thresholds above (note F1 0.98, 5 cents at the 99th percentile, 99%
   voicing agreement) and the test set's size: proposed, ten 60 s clips from
   different recordings, at least three with a confident bass line.
2. The record's software match: proposed, the torch and demucs (or pesto)
   versions must equal on both sides of the record and in the entries.
3. Thread count is mechanism, not a parameter; if bits differ by thread
   count, the same tolerance covers them and no record is needed.
4. Adding `device` to `f0-pesto` now, before bulk import, accepting that the
   shadow's `f0-pesto` entries are rekeyed and regenerated (INV-4), and only
   if a small Python environment with pesto and torch fits the Mac's disk.
