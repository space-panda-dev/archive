# 0090. An extractor run travels through Spool, and its files come back as result attachments

Status: proposed

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
