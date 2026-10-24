# Map

Where each part lives, which files are long, which tests cover it, and
what not to open. Files of 600 lines or more, and definition lines, are `wf:gen` blocks:
`lens stale --fix MAP.md` regenerates them, and the `docs` suite fails
when one is stale. Rules: [AGENTS.md](AGENTS.md);
`INV-n`: [docs/invariants.md](docs/invariants.md); ADR `nnnn`:
[docs/decisions/](docs/decisions/README.md).

## The whole in one paragraph

Archive keeps durable evidence in a Git/git-annex repository. **Door**
(Haskell, `door/`) is the only writer: every change to archival state is a
proposal that Door admits or transitions (INV-2, ADR 0002); `archive`
(`archive/app/`) creates and reads an archive. The **spec** (`spec/`) is
the contract copied into every archive: one directory per version plus content-addressed contract bundles
in `spec/contracts/`. **World** (`world/`) projects an `archive export` into
a disposable RDF store for SPARQL (ADR 0041, 0059; INV-4, INV-10).
The **relay** (`relay/`) carries Door's service, World's query, and derived
media to the operator's browser over the tailnet (ADR 0056, 0060).
**enrich** (`enrich/`) runs machine tools (probe, detect, match, feature-bank
extractors) and proposes their claims to Door under derivation runs (ADR
0064, 0073, 0074). **capture** (`capture/`) imports a recorder card as
sessions and runs the enrich chain (ADR 0070). **documents** (`documents/`)
holds importers of outside documents ([README](documents/README.md)); the iReal Pro importer, Work links,
chord grammar and exporter (ADR 0116). **migration** (`migration/`)
translates the V2 spike's records into Door proposals (ADR 0035, 0036,
0047-0049). **shadow** (`shadow/`) compares and reviews shadow runs and makes
scratch archives (ADR 0061, [runbook](docs/shadow-runbook.md)). **fixture**
(`fixture/`) builds a synthetic published archive through the real Door for
any client's tests (ADR 0059, 0062). `docs/decisions/` holds the ADRs;
`docs/proposals/` holds proposed contract bundles and plans.

## Tests at a glance

Run suites with `wf check SUITE` (names from `wf suites`). Extra arguments go
after `--`; the Python suites append them to `unittest discover`, so one
module is `wf check enrich -- -p test_bank.py` and one test name is
`-k <substring>`. Without wf: `make check`, `make test`.

<!-- wf:gen suites  -->
| suite | tier | timeout | run |
|---|---|---|---|
| check | 1 | 60s | `python3 scripts/check.py "$@"` |
| docs | 1 | 60s | `wf vet --docs "$@"` |
| build | 300 | 1800s | `cabal build -v0 lib:archive exe:door exe:archive "$@"` |
| documents | 5 | 60s | `python3 -m unittest discover -s documents/ireal -p "test_*.py" "$@"` |
| haskell | night | 3600s | `n=$#; for a do set -- "$@" "--test-option=$a"; done; shift $n; cabal test all --test-show-details=direct "$@"` |
| world | night | 3600s | `$PYTHON -m unittest discover -s world/tests "$@"` |
| relay | 300 | 1800s | `$PYTHON -m unittest discover -s relay/tests "$@"` |
| enrich-tests | 30 | 1800s | `$PYTHON -m unittest discover -s enrich/tests "$@"` |
| shadow | 300 | 1800s | `$PYTHON -m unittest discover -s shadow -p "test_*.py" "$@"` |
| documents-door | 30 | 1800s | `$PYTHON -m unittest discover -s documents/tests "$@"` |
| migration | 30 | 1800s | `$PYTHON -m unittest discover -s migration -p "test_*.py" "$@"` |
| fixture | 300 | 1800s | `$PYTHON -m unittest discover -s fixture -p "test_*.py" "$@"` |
| enrich | night | 3600s | `$ENRICH_PYTHON -m unittest discover -s enrich -p "test_*.py" "$@"` |
| capture | 300 | 1800s | `$ENRICH_PYTHON -m unittest discover -s capture -p "test_*.py" "$@"` |
| bench | 30 | 300s | `P="$HOME/archive-bench/envs/harness/bin/python"; test -x "$P" \|\| { echo "bench: no $P; the bench runs on the x86-64 laptop (wf check --on laptop)"; exit 1; }; "$P" -m unittest discover -s enrich/bench -p "test_*.py" "$@"` |
| bench-horn | night | 3600s | `P="$HOME/archive-bench/envs/harness/bin/python"; test -x "$P" \|\| { echo "bench-horn: no $P; the bench runs on the x86-64 laptop (wf check --on laptop)"; exit 1; }; ARCHIVE_BENCH_ROOT="$HOME/archive-bench" "$P" -m enrich.bench check horn-v1 "$@"` |
<!-- /wf:gen -->

`haskell` is `door-test` and `archive-test`, tasty trees whose cases are the
named checks (`wf check haskell -- -p proposalChecks` runs one); `enrich` and
`capture` run the Essentia interpreter; `bench` and `bench-horn` need the
bench's private data on the x86-64 laptop. Python suites build `door`/`archive` first and set
`ARCHIVE_REQUIRE_INTEGRATION=1` (skips become failures).

## Door and archive (Haskell): the one writer, and the reader commands

Build: [archive.cabal](archive.cabal): library `door/src`, executables
`door`, `archive`, test suites `door-test`, `archive-test`.

| File | What |
|---|---|
| `door/app/Main.hs` | `main` dispatch: `admit`, `transition` (`--dry-run`), `serve --socket`, `verify`; `proposalDryRun`, `admissionCommit`, `transitionCommit`, `serve` |
| `archive/app/Main.hs` | `main`: `init`, `maintenance repack/prune-packed`, `status`, `doctor`, `ls`, `show`, `export [--since]`; `current` is unsupported |
| `door/src/Archive/Door/AdmissionDryRun.hs` | `dryRunAdmission`; re-exports the snapshot and proposal-contract APIs below |
| `door/src/Archive/Door/Snapshot/` | `Load.loadSnapshot` (checkpointed or full read), `History` (immutable paths, upgrades, citation order), `Supersession` (heads, cycles), `Memo` (remembered snapshot, ADR 0054), `Data`, `Read` |
| `door/src/Archive/Door/ProposalContracts.hs` | `validateProposalContracts` over `ContractCitations.hs`, `ReferenceTargets.hs` (what a reference must pin) and `TargetMeasures.hs` (payload bounded by the pinned Claim) |
| `door/src/Archive/Door/AdmissionCommit.hs` | `commitAdmission`, `refreshVerificationCheckpoint` |
| `door/src/Archive/Door/TransitionDryRun.hs` | `dryRunTransition`, `planTransition`; both planners' refusals are `PlanProblem.hs`, proposal-reading refusals `ProposalProblem.hs` |
| `door/src/Archive/Door/Transition/` | `Dispatch.transitionDispatchTable` (contract id/version to kind), `Rules` (per kind), `Replay` (facts from history), `Upgrade` (prospective snapshot), `Plan`, `Problem` (rule and replay refusals) |
| `door/src/Archive/Door/TransitionCommit.hs` | `commitTransition` |
| `door/src/Archive/Door/Contracts.hs` | bundle loading; `validateSchema` checks a bundle's schema and its `x-archive-*` rule keywords; re-exports the parts below and renders their typed errors (`BundleError`) to the refusal text |
| `door/src/Archive/Door/Contracts/` | `Schema.hs` keyword checks, `Evaluate.hs` evaluator and mismatch explanation, `SchemaError.hs` and `Profile/Error.hs` the typed refusals, `Pattern.hs` regex subset, `Pointer.hs`, `Context.hs` JSON-LD contexts, `Profile.hs` the `ProfileRule` type, parsed once per bundle, and `checkInstance`; `Profile/` each rule family's syntax and evaluator |
| `door/src/Archive/Door/RecordCommit.hs` | `commitRecordBatch*`, write lock, git-annex content |
| `door/src/Archive/Door/RecordCommit/*.hs` | the writer's private parts: `Lock`, `Journal`, `JournalFormat` (its pure encoding, exposed for the properties) and `Recovery` (operation journal), `ObjectName` (journaled Git names), `Records`, `IndexReservation`, `Durability` (object store and ref sync), `Git`, `Descriptors` (fd primitives), `Paths`, `Worktree`, `Maintenance`, `Failure` (diagnostic text; an IO exception is caught and spelled by `Archive/Door/IOFailure.hs`, meant for bootstrap and proposal reading too) |
| `door/src/Archive/Door/Bootstrap.hs` | `initArchive`, rollback; steps in `Bootstrap/`: `Pinned` (writes under the root descriptor), `Journal` (ownership journal), `Git`, `Contents` (release files, bootstrap records), `Verify` (existing archive), `Tracked` (`PathTracker`, rollback), `Abort` |
| `door/src/Archive/Door/VerifiedCheckpoint.hs` | incremental verification checkpoint (ADR 0052) |
| `door/src/Archive/Door/Refusal.hs` | `RefusalCode` (ADR 0058) |
| `door/src/Archive/Read/Core.hs`, `Archive/Doctor/Checks.hs` | reading commands, `doctor` |
| `door/src/Archive/GitSnapshot.hs`, `Process.hs`, `ProcessPinned.c` | Git snapshot reads, pinned process launch (ADR 0033) |
| `door/service.schema.json` | published service and refusal codes |

Tests: suite `haskell`. `door/test/Main.hs` and `archive/test/Main.hs`
are single sequential programs of named `assert "..."` checks with no
filter; find one with `rg -n 'assert "<words>' door/test/Main.hs`.
QuickCheck properties of the pure parts (pattern engine, JSON Pointer, RFC
8785 canonicalisation, operation journal encoding, hex) sit one module per
subject in `door/test/*Properties.hs`, under door-test's `properties` group.
Rules: INV-1, INV-2, INV-5; record envelope ADR 0022; admission ADR 0023;
transitions ADR 0028, 0030; power-loss durability is accepted but not yet met
(ADR 0031, see README status); executables are configured, never taken from
`PATH` (ADR 0033); refusal codes in `Refusal.hs` must equal
`service.schema.json` (a `door-test` check).
Find: per-contract payload rules are `x-archive-*` keys in the bundle JSON,
implemented under `Contracts/Profile/` (`rg -n 'x-archive-' door/src`); transition
kinds in `transitionDispatchTable`. The span rules' list and path forms are
`SpanRules.hs`; the check beyond the circle (S-30) is `BeyondCircle.hs` over
`Reach.hs` (sensitivity, rights) and `SharingGate.hs` (co-writers' consent),
run by `planTransition` on today's UTC date, never on replay; pure checks in
`door/test/BeyondCircle010.hs`, agreement with World in
`world/tests/test_door_agreement_beyond_circle.py`.

<!-- wf:gen lines door/src/Archive/Door/AdmissionDryRun.hs door/src/Archive/Door/TransitionDryRun.hs door/src/Archive/Door/Contracts.hs door/src/Archive/Door/RecordCommit.hs door/src/Archive/Door/Bootstrap.hs door/src/Archive/Read/Core.hs door/src/Archive/GitSnapshot.hs door/src/Archive/ProcessPinned.c door/test/Main.hs archive/test/Main.hs -->
| file | lines |
|---|---|
| `door/src/Archive/Door/AdmissionDryRun.hs` | 151 |
| `door/src/Archive/Door/TransitionDryRun.hs` | 312 |
| `door/src/Archive/Door/Contracts.hs` | 332 |
| `door/src/Archive/Door/RecordCommit.hs` | 583 |
| `door/src/Archive/Door/Bootstrap.hs` | 467 |
| `door/src/Archive/Read/Core.hs` | 611 |
| `door/src/Archive/GitSnapshot.hs` | 661 |
| `door/src/Archive/ProcessPinned.c` | 799 |
| `door/test/Main.hs` | 7888 |
| `archive/test/Main.hs` | 1015 |
<!-- /wf:gen -->

<!-- wf:gen anchors door/src/Archive/Door/Contracts.hs#validateSchema door/src/Archive/Door/Transition/Dispatch.hs#transitionDispatchTable -->
- `door/src/Archive/Door/Contracts.hs#validateSchema`: line 227
- `door/src/Archive/Door/Transition/Dispatch.hs#transitionDispatchTable`: line 41
<!-- /wf:gen -->

## Spec: what every archive must satisfy, copied into each

- `spec/VERSION` (current `0.0.11`); `spec/archive-spec.md`
  is a byte copy of `spec/0.0.11/archive-spec.md`. Navigate by
  heading: `rg -n '^##' spec/archive-spec.md` (section 6, "Reading
  commands", is most of it; D-18 holds 0.0.10's contracts, D-19 0.0.11's).
  Release notes: [docs/releases/0.0.11.md](docs/releases/0.0.11.md),
  [docs/releases/0.0.10.md](docs/releases/0.0.10.md).
- `spec/<version>/contracts.json` lists
  `{id, version, digest}`; each digest names `spec/contracts/sha256-<hex>.json`.
- `spec/<version>/context.jsonld` is the JSON-LD context (ADR 0025).

<!-- wf:gen lines spec/archive-spec.md -->
| file | lines |
|---|---|
| `spec/archive-spec.md` | 2758 |
<!-- /wf:gen -->

Rules: never delete a version or a bundle an archive may cite; supersede it
(AGENTS.md, Pruning). `check` fails when a catalog entry has no bundle, a
bundle's bytes differ from its digest, a bundle is listed by no version, or
`archive.cabal` `data-files` omits a version's files. Catalogs: ADR 0057;
upgrades: ADR 0068.

## World (Python): disposable RDF projection of an export, and SPARQL

| File | What |
|---|---|
| `world/world.py` | `_main`: `build`, `rebuild`, `refresh`, `query`, `musicbrainz-work`; `_project`, `_project_review_claim`, `_project_derivation_claim`, `_project_music_claim`, `refresh_store`, `query` |
| `world/harmony.py` | chord stacks, sequences, meter (ADR 0077) |
| `world/contributions.py` | contributions, membership (ADR 0078) |
| `world/documents.py`, `notes.py`, `circle.py` | documents, timed lyrics and syllables, sensitivity and rights, `Reacher` (0085); notes, melody, `tune_notes`, `written_rhythm` (0086); sharing gate, readable set, `beyond_circle`, `may_publish` (0088, 0085) |
| `world/record_views.py`, `contract_graphs.py` | the record views (`VIEWS` at the end); `RECHECKS`, contract to checker |
| `world/conformance.py` | checks a store against `world/projection/6/` |
| `world/projection/6/*.ttl`, `projection.json` | named graphs, SHACL shapes, vocabulary |
| `world/examples/*.sparql` | example queries, checked by tests |

Tests: suite `world`; `world/tests/test_world.py`, plus `test_harmony`,
`test_contributions`, `test_derivation`, `test_door_agreement`,
`test_conformance`, `test_upgrade`, `test_music`, `test_role_scheme`.
Rules: World owns no facts and presents competing claims without choosing
(INV-10, ADR 0042); additions keep projection version 6 but raise
`PROJECTION_REVISIONS` in `world.py`; renames and removals make a new
version served beside the old for 90 days (ADR 0059); incomplete or
unsupported input fails, never an empty answer (INV-24); refresh must equal a
full rebuild (ADR 0055).

<!-- wf:gen lines world/world.py world/harmony.py world/contributions.py world/documents.py world/notes.py world/circle.py world/tests/test_world.py -->
| file | lines |
|---|---|
| `world/world.py` | 2138 |
| `world/harmony.py` | 1061 |
| `world/contributions.py` | 810 |
| `world/documents.py` | 1287 |
| `world/notes.py` | 1657 |
| `world/circle.py` | 770 |
| `world/tests/test_world.py` | 1010 |
<!-- /wf:gen -->

<!-- wf:gen anchors world/world.py#_main world/world.py#_project world/world.py#_project_review_claim world/world.py#_project_derivation_claim world/world.py#_project_music_claim world/world.py#refresh_store -->
- `world/world.py#_main`: line 2027
- `world/world.py#_project`: line 273
- `world/world.py#_project_review_claim`: line 606
- `world/world.py#_project_derivation_claim`: line 852
- `world/world.py#_project_music_claim`: line 1097
- `world/world.py#refresh_store`: line 1808
<!-- /wf:gen -->

## Relay (Python): HTTP for the browser; no state; loopback only

`relay/relay.py` (`GET /media/{digest}/bank/{extractor}/{output}/{key}` serves a region entry's file by key): `DOOR_ROUTES`, `MEDIA_ROUTES`, `ROUTES`, `Relay`, `make_handler`, `main`.
`relay/openapi.json` describes every route, answer, and refusal.
`relay/metrics.py` (`GET /metrics`): `archive_counts` (from World's records), `BankCounter`, `media_counts`, `derived_bytes`, `Collector` (background counts, request durations), `exposition`.
Tests: suite `relay`; `relay/tests/test_relay.py` (`CoherentSaveChecks`
is ADR 0113's end-to-end save on the built fixture), `test_media.py`, `test_metrics.py`. A local
probe's `GET /health` and `GET /metrics` need no login (ADR 0114, 0118). Every answer is validated against `openapi.json`, and Door refusals
against `door/service.schema.json`.
Rules: ADR 0054 (Door service), 0056, 0060 (interface and role), 0063 (read
speed), 0069 (media by digest), 0114 (local health), 0118 (metrics).

<!-- wf:gen lines relay/relay.py relay/openapi.json relay/tests/test_relay.py -->
| file | lines |
|---|---|
| `relay/relay.py` | 1292 |
| `relay/openapi.json` | 4779 |
| `relay/tests/test_relay.py` | 1194 |
<!-- /wf:gen -->

<!-- wf:gen anchors relay/relay.py#DOOR_ROUTES relay/relay.py#MEDIA_ROUTES relay/relay.py#ROUTES relay/relay.py#Relay relay/relay.py#make_handler relay/relay.py#main -->
- `relay/relay.py#DOOR_ROUTES`: line 61
- `relay/relay.py#MEDIA_ROUTES`: line 68
- `relay/relay.py#ROUTES`: line 80
- `relay/relay.py#Relay`: line 766
- `relay/relay.py#make_handler`: line 970
- `relay/relay.py#main`: line 1278
<!-- /wf:gen -->

## enrich (Python)

Purpose: machine claims proposed through Door, derived media, and the feature
bank. Doc: [enrich/README.md](enrich/README.md) (one section per command,
`rg -n '^##' enrich/README.md`).

| File | What |
|---|---|
| `enrich/enrich.py` | `main`: `probe`, `detect`, `match`, `match-windows` (`run_probe`, `run_detect`, `run_match`, `run_match_windows`) |
| `enrich/adapter.py` | `propose`: a tool result to a Door proposal (ADR 0064) |
| `enrich/propose.py`, `proposers.py` | `enrich propose` (per-region runs where the whole read lacks a feature; `Context.region`, `shift_of`); `PROPOSERS` (registry at the end): bank features as claims, the bass line and vocal bleed filter (`bass_line`, `read_bass_line`, `drop_bleed`), lines built from `f0-pesto` (`build_line`), forced alignment declared and refused `not_yet` |
| `enrich/align.py`, `align_command.py` | `enrich align` (ADR 0116 section 6): `chart_of` (World's played bars, `beats_of`), `machine_of`, `place` (DP over played bar and beat, twelve transpositions, `Refused` when unsure), `payloads` (timed meter, chord stacks, form spans); the command reads the archive and proposes one run; tests `enrich/tests/test_align.py` (suite `enrich-tests`) |
| `enrich/note_builder.py` | `build`: one note per held pitch from a pitch track and its level (ADR 0086); settings `VOICE`, `BASS`; tests `enrich/test_note_builder.py` |
| `enrich/probe.py`, `detector.py` | stream facts (0065), span suggestions (0066; presets and the talk gate, 0091) |
| `enrich/match.py`, `cover.py`, `windows.py` | work matching (0067, 0071, 0072) |
| `enrich/extract.py` | feature-bank runner: `main` (`list`, `run` with `--region` and `--exact`, `equivalence`, `region-equivalence`), `cut_region`, `run_default`, `run_from` (a region's stem measured with its `placed` region); `Run.stand_in` |
| `enrich/bank.py` | `Catalog` (`within` for regions, `placed_by` for a stem made over one), `Bank` (`candidates`, `stands_in`, `measured_over`, `probe_falls_short`): the bank format; equivalence records (`check_equivalence`, `decisions`); region keys (`key_of`, `check_region`), `RegionView`/`region_entries`/`regions` (readers of region entries; `Entry.placed`, `Entry.timeline`), `covering`, bank requests (`ask`, `requests`) at the end (ADR 0112) |
| `enrich/equivalence.py` | `measure`: the equivalence record of interchangeable entries; `MEASURES` (note F1, f0 cents, voicing); `region_measure`: a region's stems and measures against the whole run's (ADR 0112) |
| `enrich/regions.py` | song spans by precedence, `regions_of`, `regions_for`, `expensive`, `plan` per mode (ADR 0112); tests `enrich/test_regions.py` |
| `enrich/remote.py` | extractor runs through Spool: `put` (with a lossless `make_transport` of the content, remembered under `<bank>/tmp/transports/` so `put_again` does not encode it again), `work`, `collect` (ADR 0090) |
| `enrich/backfill.py` | `enrich backfill` (ADR 0112 and its amendments): `Planning`/`plan` (modes from `regions.plan`, region jobs, requests first, `request_key`), `Schedule.tier` (fast or background), `prioritise`, `projection` (time to usable, with the per-recording proposal, and to done), `Planner.state` (reads the bank through `Bank`, over a region through `Catalog.within`), `unit_order` (probes first), `layout`/`copies` (`DEVICE_SETTINGS`: stems on `cpu-inductor` one thread a copy), `check_remote` (stand-in routing), `unprobed_of`/`discover` (unprobed read from the export); re-exports the two modules below; tests `enrich/test_backfill.py` |
| `enrich/backfill_queue.py` | the backfill's pool: `Job`, `Runner` (by `Job.priority`, the background held until usable, the Spool window), `choose`/`shadow` (backfilling behind a head waiting for cores or memory; reserved outright after `reserve_after_seconds`), `take_here` (overflow of routed work when it ends here sooner), `remote_parameters` |
| `enrich/backfill_proposals.py` | the backfill's archive-side steps: `Steps` (each `call` knows its outcome), `Proposals` (usable per recording, one `--all` when more than two wait), `Follow` (`--follow` intake) |
| `enrich/media.py`, `waveform.py`, `derived.py` | derived media (ADR 0069) |
| `enrich/extractors/stems/stems.py` | `separate`; `cpu-inductor` (`compile_network`, `htdemucs_core`); a region run separates the source over a window on the whole run's grid (`aligned`, `context_of`, `trimmed`) so its stems are the whole's; tests `enrich/test_stems.py` |
| `enrich/support.py` | test support: synthetic audio, scratch archives |
| `enrich/extractors/<id>/` | `extractor.json` + program; the longest are below |

Tests: suite `enrich`, `enrich/test_<topic>.py` by command or extractor
family (`git ls-files 'enrich/test_*'`; `test_bank` is the longest, `test_extractors`
runs all extractors together); suite `enrich-tests` is
`enrich/tests/` (`test_media.py`, `test_align.py`).
The bench (`enrich/bench/`, [README](enrich/bench/README.md)) scores note
transcribers against private sets outside the repository, and gates the
backfill's CPU seconds per audio-second (`cost.py`, `python -m enrich.bench cost`), and checks a region's
entries against the whole run's (`region.py`, `python -m enrich.bench region`), and scores machine
chords, key, meter, downbeats and sections against charts placed on recordings (`chords.py`, `python -m
enrich.bench chords`, ADR 0116); suites `bench` and
`bench-horn`. The `enrich` suite does not discover it (no `__init__.py`).
Rules: enrichment never changes its inputs (INV-9); every claim carries its
run and provenance (ADR 0064, [provenance](docs/provenance.md)); an
extractor's version changes whenever its outputs can (ADR 0074); stems are
content (ADR 0075); preferred inputs ADR 0076.
Stand-in stems by a measured tolerance (ADR 0090's amendment) are built:
`interchangeable` in a manifest, `extract.py equivalence`, lookup in
`Bank.candidates`; tests `enrich/test_equivalence.py`
([format](docs/feature-bank.md#interchangeable-entries)). Bulk import (ADR
0112) is built: `enrich backfill` with its modes, region entries, bank
requests and song regions, the fast tier first (every recording usable)
and the background by priority, and `--follow`; what is left (readers of
region entries, the region stems mismatch) is step 0 of
[docs/backlog.md](docs/backlog.md).
Pitfall: the backfill's job events are `ran`/`failed`; `done` is only its
last line.
Find: extractors in `enrich/extractors/<id>/extractor.json`; SHIPPED digest
pins in `enrich/test_bank.py` (`SHIPPED`); byte format in
[docs/feature-bank.md](docs/feature-bank.md) (shipped table at "The
shipped extractors").

<!-- wf:gen lines enrich/README.md enrich/enrich.py enrich/adapter.py enrich/proposers.py enrich/detector.py enrich/cover.py enrich/extract.py enrich/bank.py enrich/remote.py enrich/backfill.py enrich/backfill_queue.py enrich/extractors/stems/stems.py enrich/extractors/quality/quality.py enrich/extractors/tonal/tonal.py enrich/extractors/structure/structure.py enrich/extractors/rhythm/meter.py enrich/test_bank.py docs/feature-bank.md -->
| file | lines |
|---|---|
| `enrich/README.md` | 1228 |
| `enrich/enrich.py` | 738 |
| `enrich/adapter.py` | 609 |
| `enrich/proposers.py` | 1059 |
| `enrich/detector.py` | 674 |
| `enrich/cover.py` | 1073 |
| `enrich/extract.py` | 1060 |
| `enrich/bank.py` | 1498 |
| `enrich/remote.py` | 720 |
| `enrich/backfill.py` | 1223 |
| `enrich/backfill_queue.py` | 613 |
| `enrich/extractors/stems/stems.py` | 755 |
| `enrich/extractors/quality/quality.py` | 1317 |
| `enrich/extractors/tonal/tonal.py` | 993 |
| `enrich/extractors/structure/structure.py` | 871 |
| `enrich/extractors/rhythm/meter.py` | 821 |
| `enrich/test_bank.py` | 1331 |
| `docs/feature-bank.md` | 633 |
<!-- /wf:gen -->

<!-- wf:gen anchors enrich/enrich.py#main enrich/bank.py#Catalog enrich/bank.py#Bank enrich/bank.py#check_equivalence enrich/test_bank.py#SHIPPED -->
- `enrich/enrich.py#main`: line 643
- `enrich/bank.py#Catalog`: line 452
- `enrich/bank.py#Bank`: line 1106
- `enrich/bank.py#check_equivalence`: line 909
- `enrich/test_bank.py#SHIPPED`: line 115
<!-- /wf:gen -->

## capture (Python)

Purpose: import a recorder card as sessions, then run probe/detect/match.
`capture/capture.py`: `main` (`import`), `read_directory`, `group`,
`plan`, `admit`, `enrich`; `grammar.py` recorder file-name grammars,
`GRAMMARS`; `send.py` sends a card read elsewhere.
Tests: suite `capture`; `test_capture`, `test_grammar`, `test_send`.
Rules: ADR 0070; capture proposes, Door commits (INV-2).

<!-- wf:gen lines capture/capture.py capture/test_capture.py -->
| file | lines |
|---|---|
| `capture/capture.py` | 767 |
| `capture/test_capture.py` | 943 |
<!-- /wf:gen -->

## documents (Python)

Purpose: importers of outside documents (ADR 0116).
`documents/documents.py`: the command line, `ireal import|candidates|link`.
`documents/ireal/codec.py`: `decode_playlist`, `chart_link`, `encode_chart`,
`encode_playlist`, `unscramble`; `chart.py`: `read_chart`, `UnsupportedChart`;
`symbols.py`: `GRAMMAR_VERSION`, `QUALITIES`, `read_symbol`, `reading_of`,
`symbol_of`, `read_key`; `claims.py`: `chart_claims` (chord sequence, form,
written meter layer, key sequence), `PARAMETERS`; `batches.py`: Door proposal
directories with revisions planned ahead, `combined` for a dry run's groups;
`importer.py`: `run_import` (playlist and chart documents, `ireal-chart` runs,
quiet reruns); `links.py`: `candidates` (a private candidates file and a
decisions template), `run_link` (the operator's Work links, composer
contributions, `lead_sheet`); `export.py`:
exporter `lead-sheet-ireal` 1 (`render`, `playlist`, `page`, `main` as
`relay/exporting.py` runs an exporter); `stand_in.py`: World's records of a chart
as the importer reads it, for the exporter's round trip.
Tests: suite `documents` (tier 5, local; `documents/ireal/test_*.py`,
readings checked with World's `check_reading_v2` and `check_key_reading`);
suite `documents-door` (tier 30, prelude; `documents/tests`: import, rerun,
link and candidates through the real Door in a scratch archive);
`test_export` reads, exports and reads again: equal in claims. Invented
charts only: no real titles or charts.

## migration (Python)

Purpose: read-only V2 census and the V2 audio route's Door proposals.
`v2_translate.py`: `main` (`plan`, `verify`, `probe`); `v2_bundle.py`
frozen bundle (ADR 0036); `v2_lookup.py`; `census.py`; `audio_route.py`
(ADR 0040, 0047); `source_manifest.py`.
Tests: suite `migration`; `git ls-files 'migration/test_*'` (six modules).
Rules: translate V2 into this vocabulary; V2 never answers an open question
(ADR 0015); evidence and claims are preserved (ADR 0035); unit status in
[docs/port-ledger.md](docs/port-ledger.md).

<!-- wf:gen lines migration/v2_translate.py migration/v2_bundle.py migration/census.py -->
| file | lines |
|---|---|
| `migration/v2_translate.py` | 1218 |
| `migration/v2_bundle.py` | 1009 |
| `migration/census.py` | 600 |
<!-- /wf:gen -->

## shadow and fixture (Python)

shadow: `compare.py` manifests and census reports; `review.py` validates
human checkpoint reviews; `scratch.py` scratch archives (ADR 0061). Suite `shadow`: `test_compare`, `test_review`, `test_scratch`.
Procedure: [docs/shadow-runbook.md](docs/shadow-runbook.md).

fixture: `build.py` (`main`) admits `plan.json` through the real Door;
`serve.py` builds (or, with `--built`, takes) and serves it with the relay
(ADR 0062): `wf run`, and `wf lease relay` for a client's worktree. Suite
`fixture`: `test_fixture`. `scripts/prove.py` prints Door's and Archive's
commands over it, normalised: `wf prove --golden` against `fixture/golden/`. The plan cites every contract in the current
catalog except the specification upgrade, so a new contract needs a plan step.

<!-- wf:gen lines fixture/plan.json -->
| file | lines |
|---|---|
| `fixture/plan.json` | 710 |
<!-- /wf:gen -->

## Do not read

| Path | Instead |
|---|---|
| `spec/contracts/sha256-*.json` (one-line bundles) | `rg -l '"<contract id>"' spec/contracts`, or the catalog `spec/<v>/contracts.json` |
| `spec/0.0.*/archive-spec.md` (old versions) | read `spec/archive-spec.md` by heading; `git diff --no-index spec/0.0.8/archive-spec.md spec/0.0.9/archive-spec.md` for a change |
| `world/tests/fixtures/*.json` (one-line exports, up to 133 KB) | `python3 -c` with `json` to count or pick; regenerate via `door-test` with `ARCHIVE_TEST_WORLD_EXPORT` (world/README.md, Tests) |
| `fixture/content/*` (binary) | `fixture/README.md` says how each was made |
| `door/test/Main.hs`, `RecordCommit.hs`, `Contracts.hs` (the longest Haskell) | `rg -n` the function or assert text, then read around it |
| `relay/openapi.json` | `python3 -c` with `json` on `["paths"]["/route"]` |
| `docs/port-ledger.md` | `rg -n '<unit>' docs/port-ledger.md` |
| `flake.lock`, `dist-newstyle/`, `cabal.project.local` | not source; never edit by hand |
| SHIPPED digests in `enrich/test_bank.py` | let the test print the new digest |

## Slow things

Each suite's tier is its measured time (`.workshop.toml` has the figures;
`wf suites` shows the last run). `enrich` is the longest: run one module
with `-- -p`. `haskell` has no filter. An unchanged tree is answered from
cache.

## Common tasks

1. **Add an extractor.** New `enrich/extractors/<id>/extractor.json` and
   program; a runner test (pattern in `enrich/test_bank.py`); a `SHIPPED`
   entry; a row in `docs/feature-bank.md` shipped table and in the
   `enrich/README.md` extractor table. Governs:
   [docs/adding-an-extractor.md](docs/adding-an-extractor.md), ADR 0074.
2. **Bump an extractor version.** Edit `version` in its `extractor.json`;
   update its `SHIPPED` pin in `enrich/test_bank.py` and its row in
   `docs/feature-bank.md` with a line on what changed. Same doc, step 5.
3. **Add a contract or claim type.** ADR first; bundle JSON (draft in
   `docs/proposals/<adr>-contracts/`), then `spec/contracts/sha256-*.json`,
   a new `spec/<v>/` (`VERSION`, `archive-spec.md`, `context.jsonld`,
   `contracts.json`), `spec/VERSION`, `spec/archive-spec.md`, `archive.cabal`
   data-files; Door rules in `Contracts.hs`/`TargetMeasures.hs` with
   `door/test/Main.hs`; a step in `fixture/plan.json`; World projection. Model:
   the commit that added `spec/0.0.9/VERSION` (spec 0.0.9). Governs: ADR 0057, 0068, 0027.
4. **Add a World view of a claim.** `_project_*_claim` in `world/world.py`
   (or a rules module like `harmony.py`), shapes and terms in
   `world/projection/6/*.ttl`, raise `PROJECTION_REVISIONS`, an example in
   `world/examples/`, tests in `world/tests/`, `world/README.md`. Governs:
   ADR 0059, INV-10, INV-11.
5. **Add a relay route.** `ROUTES`/`DOOR_ROUTES`/`MEDIA_ROUTES` and the
   handler in `relay/relay.py`, the path in `relay/openapi.json`, a test in
   `relay/tests/test_relay.py`, the route list in `relay/README.md`. Governs:
   ADR 0060.
6. **Change a Door rule.** A payload rule: the `x-archive-*` keyword in
   `Contracts.hs`, cited by a bundle (a changed bundle is a new contract
   version, never an edit). A rule reading a pinned record: `ReferenceTargets.hs`,
   `TargetMeasures.hs`. Admission flow: `AdmissionDryRun.hs`. A new
   refusal: `Refusal.hs` and `door/service.schema.json`. Tests in
   `door/test/Main.hs`. Governs: docs/invariants.md, ADR 0023, 0058.
7. **Add a transition kind.** `transitionDispatchTable` in
   `Transition/Dispatch.hs`, its rules in `Transition/Rules.hs`, and
   `TransitionCommit.hs`. Governs: ADR 0028, 0030.
