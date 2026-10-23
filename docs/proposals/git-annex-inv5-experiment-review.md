# Git-annex INV-5 experiment

This experiment checks the git-annex history effects of a small content
admission sequence. Its harness uses only a fresh temporary Git repository and
a local directory special remote. It has no code path for an archive path or a
network remote.

Run it with the operator-selected executable:

```sh
ARCHIVE_GIT_EXECUTABLE=/absolute/path/to/git \
ARCHIVE_GIT_ANNEX_EXECUTABLE=/absolute/path/to/git-annex \
  python3 scripts/annex_content_admission_experiment.py
```

The output is JSON. It identifies both executable paths and versions, the
synthetic content digest and annex key, and each `refs/heads/git-annex` commit and tree
before and after an operation. For every tree it records each blob's byte size
and SHA-256, plus a digest over the sorted path/mode/size/blob-digest list. To
save a fresh machine-readable result, pass `--output <path>`; choose a path
outside the source tree for ephemeral runs.

## Recorded synthetic run

The checked-in machine-readable report is
[git-annex-inv5-synthetic-report.json](git-annex-inv5-synthetic-report.json).
It was produced on Darwin arm64 with git-annex 10.20260901 and Git 2.55.0.
The synthetic `payload.bin` was 47 bytes, SHA-256
`ac78e6437d1967e51ca199d585c809045e107de8f524cbb84bf303dda33b742e`, with annex
key `SHA256E-s47--ac78e6437d1967e51ca199d585c809045e107de8f524cbb84bf303dda33b742e.bin`.

| Operation | Branch changed? | Evidence |
|---|---:|---|
| First `get --from=synthetic payload.bin` | Yes | Commit `484eb2a7` → `2ce6efb9`; branch byte digest `4fd990c1…` → `99e4cda1…` | <!-- lens: allow stale -->
| Identical `get` retry | No | Commit and branch byte digest unchanged |
| `whereis --json` twice | No | Commit and branch byte digest unchanged for each call |
| `info --json` twice | No | Commit and branch byte digest unchanged for each call |
| `fsck --from=synthetic` first run | Yes | Commit `2ce6efb9` → `45941b4a`; branch byte digest `99e4cda1…` → `25983623…` | <!-- lens: allow stale -->
| Identical `fsck --from=synthetic` retry | Yes | Commit `45941b4a` → `57e7f60c`; branch byte digest `25983623…` → `3a0e8b82…` | <!-- lens: allow stale -->

The complete refs, blob hashes, commands, and fixture details are in the JSON
report. The retrieved file's byte hash matched the synthetic fixture. This
reproduces the local observation that first `get` records a location, its
immediate retry is quiet, the selected read-only queries are quiet, and each
`fsck --from` updates the git-annex branch.

## Admission-sequence run on real content (2026-10-14)

The operator approved using the archive machine's Btrfs `/home` space for this run on
2026-10-14. It exercised ADR 0023's admission steps rather than only `get`
and `fsck`. A fresh `git init` plus `git-annex init` stood in for the Door
host, because no Archive with admitted content exists yet to copy (see the
gate below). A separate git-annex repository stood in for the staging host and
was added as a plain local Git remote. Its content was three Btrfs reflink
copies of files from the disposable originals copy, never the source root:
one empty, one of a few hundred bytes, and one of about 2 GB. Git 2.55.0 and git-annex
10.20260901 matched the synthetic run. Git configuration was isolated, hooks
were disabled, and no network remote existed.

The whole sequence ran once and then again on unchanged inputs:

| Step | Pass 1 | Pass 2 (unchanged inputs) |
|---|---|---|
| `git fetch staging` | quiet | quiet |
| `git annex merge` (first contact) | writes `uuid.log` and each key's location log, learned from staging | quiet |
| `git annex get --key <key> --from staging` | writes each key's location log | quiet |
| Hash `git annex contentlocation <key>`; compare SHA-256 and size with the key | quiet | quiet |
| `git annex fsck --key <key>` (local) | writes `activity.log` | **writes `activity.log`** |
| `git annex fromkey --force <key> <path>` | quiet (stages only) | quiet |
| Git commit of staged paths | commits | quiet (nothing staged) |
| `git annex whereis --key <key>` | quiet | quiet |

Findings:

1. Every step ADR 0023 needs keeps INV-5 on rerun except git-annex's own
   local `fsck`, which appends to `activity.log` on every run. Door's step 4
   fixity check therefore cannot be `git annex fsck`. Hashing the object at
   `contentlocation` and comparing SHA-256 and size with the proposal is
   read-only. It verified all three keys, including the zero-byte key.
2. The first contact with a staging remote merges that remote's `uuid.log`
   and location logs into the archive's git-annex branch, before any record is
   admitted. This write happens once and is quiet afterward. Whether it counts
   as meaningful history or operational state is not settled here.
3. `get` records the archive's own copy in the location log before Door's
   Git commit. ADR 0023 already says that content transferred before a failed
   commit stays reported, unadmitted storage.

The retained command script, its output, the final Git `HEAD`
(`3f1fa8b6`), the `git-annex` ref (`91edc2a9`), and the annex UUID are kept <!-- lens: allow stale -->
outside this repository with the run. The script's SHA-256 is
`da53b1b9…c0451`, and the output's is `364517fd…36199`. <!-- lens: allow stale -->

## Review record for the real-archive-copy gate

The synthetic run does not satisfy the open question's requirement to run the
selected operations twice on a copy of a real archive. Before that run, fill in
and review this record. Use only an operator-approved disposable copy. Keep the
original archive and its staging hosts out of the command paths.

| Field | Review value |
|---|---|
| Operator approving this run and date | _pending_ |
| Archive identity and source-copy generation | _pending_ |
| Disposable copy path and proof it is isolated | _pending_ |
| Git executable path and version | _pending_ |
| Git-annex executable path and version | _pending_ |
| Git HEAD and complete refs before copy | _pending_ |
| Annex UUID and `git-annex` ref before copy | _pending_ |
| Copy completeness evidence: referenced records, annex keys, local bytes | _pending_ |
| Exact command argv, in order, including the retry for each operation | _pending_ |
| Remotes each command can contact; local-only restriction evidence | _pending_ |
| Baseline and per-command ref, tree, and byte-hash report location | _pending_ |
| Cleanup or retained-copy decision and responsible person | _pending_ |

Take and record a baseline of the disposable copy's refs and byte hashes before
running commands. Capture the same evidence after every command and after its
identical retry. Run the sequence again from a fresh copy of the same approved
source snapshot if the first run writes an unexpected ref or cannot prove its
scope. Record every git-annex branch change, including `activity.log`; do not
classify a change as harmless by assumption. The review must identify which
operations apply will actually use and decide whether those operations can
preserve INV-5 or need an explicit operational-state rule.

This record is a review template, not approval to touch an archive. No real
archive copy was used for the recorded synthetic run. The 2026-10-14 run used
real content but a fresh repository, because no Archive with admitted content
exists yet; this gate cannot be met literally until content admission is
implemented. On 2026-10-14 the operator accepted the real-content run as
meeting this gate for content admission. The table above remains the record
for any later run that apply's operations need. Door now admits content this
way. It has git-annex hash the object with `calckey --backend=SHA256` instead
of `fsck`, so content bytes stay out of Door's own memory (D-4). A Door test
fails if admission ever invokes `fsck`. ADR 0031's power-loss durability
contract remains unverified.
