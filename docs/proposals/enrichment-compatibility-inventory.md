# Enrichment compatibility inventory

Implementation inventory for proposed [ADR 0120](../decisions/0120-internal-enrichment-composition.md).
Source references describe this branch at inventory time, not live fleet state.
No audio jobs, host changes or archival writes were performed for this inventory.

## Every registered proposer

Registry: `enrich/proposers.py:1046`; produced contract constants: lines 60–65.
All implemented proposers run first on whole content. `propose_artifact`
(`enrich/propose.py:174`) retries regions through Bank.RegionView only when the
whole attempt returns missing features. Valid empty output does not trigger
region fallback. Context.bounds/sample clamps observations to the region;
shift_of and entry offsets preserve absolute stream positions. Region
availability does not guarantee every method can produce useful observations.

| Method/version | Contract id suffix/version | Mandatory reads and placement | Alternatives, optional reads and empty behavior |
|---|---|---|---|
| chords-jiang/1 | chord_stacks/2 | Mix chords-jiang: chords and readings file (`proposers.py:256`) | Reads must describe spans one-for-one; malformed file errors. No readable chord spans gives Lacking detail. |
| chords-chordino/1 | chord_stacks/2 | Mix chords-chordino: chords (`:295`) | No spans gives Lacking detail. No optional features. |
| beats/1 | meter_layers/2 | Mix rhythm: pulse, downbeats (`:358`) | Pulse required for a claim; absent bars still permits beat-only layer. No pulse in bounds gives Lacking detail. |
| bass/1 | chord_stacks/2 | stems/bass file; tonal bass_chroma and pitched measured over that same stem; mix rhythm/pulse (`:446`) | First measured stem candidate satisfying tonal, else first candidate for missing diagnostics. No leading pitched pulse interval gives Lacking detail. Shared frame grid required. |
| key/2 | key_regions/1 | Mix tonal/key_regions (`:801`) | No labeled region gives Lacking detail; too many regions errors. Confidence weighting depends on tonal version. |
| tuning/1 | tuning/1 | Mix tonal: tuning_cents, tuning_confidence, tuning_drift, tuning_windows (`:843`) | All four are recorded; missing message omits tuning_windows. No confidence gives Lacking detail. Drift/window mismatch errors. |
| notes/3 | notes/1 | Bank.default_entry(melody), its source file if selected; melody/notes OR vocal f0-pesto f0 and level_db (`:910`) | Detailed selection below. Empty selected track does not fall back to another algorithm. Too many notes errors. |
| bass-line/2 | notes/1 | stems/bass; tonal bass_chroma/pitched on same stem; f0-pesto f0/level_db OR notes-basic-pitch/notes on that stem (`:564`, `:614`) | First complete measured stem candidate, else first candidate. PESTO wins when present; basic-pitch only if absent. PESTO optionally reads recording tonal tuning through build_line. Empty/agreement-filtered line gives Lacking detail; no tempo read on this path. |
| sections/1 | form_spans/1 | Mix structure/sections (`:992`) | Only valid bounded labels/spans survive; no surviving section gives Lacking detail. |
| timed-lyrics/0 | No implemented output | No bank reads (`:1039`) | Always not_yet with ALIGNMENT_NEEDS; aligner, text input, syllabification and document pin remain unsupported. Intended lyrics contract is not an implemented production promise. |

All contract ids above have prefix `archive.claim/`. Proposer versions are
method versions, not extractor or output contract versions.

### Notes and conditional reads

1. `Bank.default_input/default_entry` (`enrich/bank.py:1246`, `:1316`) selects
   an available preferred file feature according to manifest quality thresholds
   and probe rules; otherwise mix. A selected stem lacking measurement does
   not silently fall back to cached mix. Preference never schedules its source.
2. For a selected vocal feature, notes checks PESTO on that feature first. If
   present, build_line reads f0 and level_db (`proposers.py:675`); otherwise it
   reads the selected melody/notes. It finds and cites the actual stems entry
   whose output digest was measured, including an equivalent device candidate.
3. PESTO build_line optionally reads mix tonal/tuning_confidence and, only when
   confidence/cents permit, tuning_cents (`:645`). Default tuning is zero.
   Voice additionally reads rhythm/pulse for half-median tempo (`:659`), but
   only records the read when at least two numeric pulse times exist.
4. A vocal line checks bass_line_entries (`:564`) as optional bleed support.
   When complete, read_bass_line (`:587`) reads stem tonal and either PESTO or
   basic-pitch, plus optional mix tuning on its PESTO path. The notes proposer
   does not separately record bass stem file as bass-line itself does; preserve
   current provenance pending a separately versioned correction if desired.
   With a complete bass line it records vocal level_db and loud_db (`:976`).
   Without one, bass_match is omitted, and existing range filtering still runs.
5. Empty vocal PESTO, empty melody notes, or bleed removing all notes returns
   Lacking detail with no alternative computation. Optional reads affect claim
   settings/provenance and must survive the shared resolver refactor.

### Semantic dependencies

`proposers.py` imports Bank for feature lookup, chord_labels for chord meaning,
and note_builder for the pitch-to-note algorithm. Its lazy `world_module`
(`:50`, `:787`) reads `world.tonality.MODES`, a pure vocabulary table, not World
projection state. Keep that meaning Archive-side; importing the table is not a
reason to move World into extraction. Archive-specific proposer output contracts,
stream bounds and Door packaging remain outside standalone production.

## Existing callers and configuration

| Caller | Existing behavior to preserve | Source |
|---|---|---|
| Capture | TARGETS chords-jiang, chords-chordino, rhythm, tonal; bank optional; no stems or structure inferred; default attempts chords-jiang, chords-chordino, beats, bass | `capture/propose_step.py:37`, `:50`, `:91`; `proposers.py:1059` |
| Capture chain | Probe, detect, peaks/listening copies, match, then feature/propose step; same intake and failure behavior | `capture/capture.py:428` |
| Backfill | Attempts nine implemented proposers; features include stems, rhythm, tonal, both chord extractors, structure, vocal PESTO, melody, bass tonal, bass PESTO, bass basic-pitch | `enrich/backfill.py:133`, `:139` |
| Backfill config | units are extractor/source pairs; schedule/extractor resources/routes separate from proposer list; explicit empty --proposers suppresses proposals | `backfill.py:289`, `:935`, `:1063` |
| Region selection | Artifact/stream discovery and accepted/suggested song regions remain Archive-side; songs/eager/on-demand retain their current behavior | `backfill.py:430`, `:458`, `:718`; `enrich/regions.py` |
| Propose command | Registry callable dispatch, actual feature provenance, previous equivalent run suppression and Door admission; regions only after whole missing | `enrich/propose.py:125`, `:163`, `:174`, `:205` |
| Feature-only readers | Relay derived playback, requests/covering lookup, peaks, listening copies and analysis features need no immediate claim consumer | `enrich/bank.py:1398`, `:1445`; `enrich/media.py`; `relay/tests/test_media.py` |

`extract.load_config` (`:928`) accepts programs, parameters, extractors,
backfill only. `programs_of` (`:861`) and Catalog own program/parameter precedence;
ADR 0120 adds selection fields without changing those identities. Backfill
`unit_order/probe_units` (`:553`, `:563`) preserves vocal PESTO-before-melody
quality probing. This ordering is observable work even without immediate claims.

## Execution, freshness and state seams

- Catalog (`bank.py:452`) owns manifest validation, dependency closure, overrides
  and keys. Bank.entry/candidates/measured_over/default_entry own configured
  entries, equivalence and source selection (`:1118`, `:1132`, `:1216`, `:1316`).
- extract.Run.graph, run_one, make (`extract.py:435`, `:527`, `:547`) validate
  identities/results and reuse entries. verify_digest (`:423`) hashes before
  actual extraction; a warm cached run with a supplied digest does not hash.
  run_from/run_default (`:707`, `:762`) own file-feature and preferred input work.
- remote.put (`remote.py:333`) hashes input and validates task request;
  work (`:451`) validates attachment digest and reconstructs configured keys;
  file_result (`:516`) validates attachments and result data before filing.
  Only extractors without feature dependencies travel (`remote.py:140`); preserve task payload, capability names, transport, cancellation and failure logs. Catalog and configuration are frozen for a run, not live-reloaded.
- backfill.Planner.state (`:506`), Planning.want (`:622`) and backfill_queue.Runner
  own state reconciliation and dispatch; backfill_proposals.Proposals/Follow own
  proposal cadence, refresh and follow intake. No second scheduler is needed.
- propose.run (`propose.py:205`) reads current heads; equivalent-run lookup and
  reader.refresh (`:163`, `:168`) avoid duplicate admission. Actual stream and
  Artifact pins and feature citations are built by propose_one and proposers.read
  (`propose.py:125`, `proposers.py:186`). This is not a global transaction.

Standalone bank/extract/remote need no archive reader, Door or World projection.
Runner verification and characterization evidence are separate workflow outputs;
this document does not claim those tests passed before their owner supplies them.
Admitted claims/runs are evidence; bank entries regenerable; dispatch/report
state operational. Policy evaluation and interrupted capture recovery remain
[open questions](../open-questions.md).
