# Map

Where each part lives, which files are long (line counts from `wc -l`),
which tests cover it, and what not to open. Rules: [AGENTS.md](AGENTS.md);
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
sessions and runs the enrich chain (ADR 0070). **migration** (`migration/`)
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

| Suite | Runs | Timeout |
|---|---|---|
| `check` | `python3 scripts/check.py` (docs, links, orphans, spec, licences) | 300 s |
| `haskell` | `cabal test all` (`door-test`, `archive-test`) | 3600 s |
| `world` | `unittest discover -s world/tests` | 3600 s |
| `relay` | `unittest discover -s relay/tests` | 1800 s |
| `enrich-tests` | `unittest discover -s enrich/tests` (derived media) | 1800 s |
| `enrich` | `unittest discover -s enrich -p "test_*.py"` (Essentia interpreter) | 3600 s |
| `capture` | `unittest discover -s capture -p "test_*.py"` (Essentia interpreter) | 1800 s |
| `bench` | `unittest discover -s enrich/bench` (the bench's own interpreter) | 300 s |
| `bench-horn` | `python -m enrich.bench check horn-v1`: private data, x86-64 laptop only; fails on a regression | 3600 s |
| `migration`, `shadow`, `fixture` | `unittest discover -s <dir> -p "test_*.py"` | 1800 s |

Python suites build `door`/`archive` first and set
`ARCHIVE_REQUIRE_INTEGRATION=1` (skips become failures).

## Door and archive (Haskell): the one writer, and the reader commands

Build: [archive.cabal](archive.cabal) (149): library `door/src`, executables
`door`, `archive`, test suites `door-test`, `archive-test`.

| File | Lines | What |
|---|---|---|
| `door/app/Main.hs` | 277 | `main` dispatch: `admit`, `transition` (`--dry-run`), `serve --socket`, `verify`; `proposalDryRun`, `admissionCommit`, `transitionCommit`, `serve` |
| `archive/app/Main.hs` | 373 | `main`: `init`, `maintenance repack/prune-packed`, `status`, `doctor`, `ls`, `show`, `export [--since]`; `current` is unsupported |
| `door/src/Archive/Door/AdmissionDryRun.hs` | 1574 | `dryRunAdmission`, `loadSnapshot`, `validateProposalContract` |
| `door/src/Archive/Door/AdmissionCommit.hs` | 335 | `commitAdmission`, `refreshVerificationCheckpoint` |
| `door/src/Archive/Door/TransitionDryRun.hs` | 1119 | `dryRunTransition`, `transitionDispatchTable` (contract id/version to kind) |
| `door/src/Archive/Door/TransitionCommit.hs` | 114 | `commitTransition` |
| `door/src/Archive/Door/Contracts.hs` | 2448 | bundle loading; `validateSchema` (line ~274) parses the `x-archive-*` rule keywords; `validate*Rule` functions implement them |
| `door/src/Archive/Door/RecordCommit.hs` | 3015 | `commitRecordBatch*`, write lock, git-annex content |
| `door/src/Archive/Door/Bootstrap.hs` | 1627 | `initArchive` |
| `door/src/Archive/Door/VerifiedCheckpoint.hs` | 416 | incremental verification checkpoint (ADR 0052) |
| `door/src/Archive/Door/Refusal.hs` | 120 | `RefusalCode` (ADR 0058) |
| `door/src/Archive/Read/Core.hs`, `Archive/Doctor/Checks.hs` | 617, 444 | reading commands, `doctor` |
| `door/src/Archive/GitSnapshot.hs`, `Process.hs`, `ProcessPinned.c` | 660, 320, 799 | Git snapshot reads, pinned process launch (ADR 0033) |
| `door/service.schema.json` | 304 | published service and refusal codes |

Tests: suite `haskell`. `door/test/Main.hs` (7827) and `archive/test/Main.hs`
(1001) are single sequential programs of named `assert "..."` checks with no
filter; find one with `rg -n 'assert "<words>' door/test/Main.hs`.
Rules: INV-1, INV-2, INV-5; record envelope ADR 0022; admission ADR 0023;
transitions ADR 0028, 0030; power-loss durability is accepted but not yet met
(ADR 0031, see README status); executables are configured, never taken from
`PATH` (ADR 0033); refusal codes in `Refusal.hs` must equal
`service.schema.json` (a `door-test` check).
Find: per-contract payload rules are `x-archive-*` keys in the bundle JSON,
implemented in `Contracts.hs` (`rg -n 'x-archive-' door/src`); transition
kinds in `transitionDispatchTable`. The span rules' list and path forms are
`SpanRules.hs`; the check beyond the circle (S-30) is `BeyondCircle.hs` over
`Reach.hs` (sensitivity, rights) and `SharingGate.hs` (co-writers' consent),
run by `planTransition` on today's UTC date, never on replay; pure checks in
`door/test/BeyondCircle010.hs`, agreement with World in
`world/tests/test_door_agreement_beyond_circle.py`.

## Spec: what every archive must satisfy, copied into each

- `spec/VERSION` (current `0.0.10`, unreleased); `spec/archive-spec.md`
  (2620) is a byte copy of `spec/0.0.10/archive-spec.md`. Navigate by
  heading: `rg -n '^##' spec/archive-spec.md` (section 6, "Reading
  commands", runs from line 757 to 2602; D-18 holds 0.0.10's contracts).
  Release notes: [docs/releases/0.0.10.md](docs/releases/0.0.10.md).
- `spec/<version>/contracts.json` (0.0.10: 315 lines, 62 contracts) lists
  `{id, version, digest}`; each digest names `spec/contracts/sha256-<hex>.json`.
- `spec/<version>/context.jsonld` is the JSON-LD context (ADR 0025).

Rules: never delete a version or a bundle an archive may cite; supersede it
(AGENTS.md, Pruning). `check` fails when a catalog entry has no bundle, a
bundle's bytes differ from its digest, a bundle is listed by no version, or
`archive.cabal` `data-files` omits a version's files. Catalogs: ADR 0057;
upgrades: ADR 0068.

## World (Python): disposable RDF projection of an export, and SPARQL

| File | Lines | What |
|---|---|---|
| `world/world.py` | 2138 | `_main` (line ~2013): `build`, `rebuild`, `refresh`, `query`, `musicbrainz-work`; `_project` (271), `_project_review_claim` (592), `_project_derivation_claim` (838), `_project_music_claim` (1083), `refresh_store`, `query` |
| `world/harmony.py` | 889 | chord stacks, sequences, meter (ADR 0077) |
| `world/contributions.py` | 810 | contributions, membership (ADR 0078) |
| `world/documents.py`, `notes.py`, `circle.py` | 1081, 1365, 765 | documents, timed lyrics and syllables, sensitivity and rights, `Reacher` (0085); notes, melody, `tune_notes`, `written_rhythm` (0086); sharing gate, readable set, `beyond_circle`, `may_publish` (0088, 0085) |
| `world/record_views.py`, `contract_graphs.py` | 588, 565 | the record views (`VIEWS` at the end); `RECHECKS`, contract to checker |
| `world/conformance.py` | 176 | checks a store against `world/projection/6/` |
| `world/projection/6/*.ttl`, `projection.json` | 18-440 | named graphs, SHACL shapes, vocabulary |
| `world/examples/*.sparql` | | example queries, checked by tests |

Tests: suite `world`; `world/tests/test_world.py` (1010), plus `test_harmony`,
`test_contributions`, `test_derivation`, `test_door_agreement`,
`test_conformance`, `test_upgrade`, `test_music`, `test_role_scheme`.
Rules: World owns no facts and presents competing claims without choosing
(INV-10, ADR 0042); additions keep projection version 6 but raise
`PROJECTION_REVISIONS` in `world.py`; renames and removals make a new
version served beside the old for 90 days (ADR 0059); incomplete or
unsupported input fails, never an empty answer (INV-24); refresh must equal a
full rebuild (ADR 0055).

## Relay (Python): HTTP for the browser; no state; loopback only

`relay/relay.py` (1065): `DOOR_ROUTES`, `MEDIA_ROUTES`, `ROUTES` (lines
44-54), `Relay` (641), `make_handler` (827), `main` (1051).
`relay/openapi.json` (1935) describes every route, answer, and refusal.
Tests: suite `relay`; `relay/tests/test_relay.py` (911), `test_media.py`
(467). Every answer is validated against `openapi.json`, and Door refusals
against `door/service.schema.json`.
Rules: ADR 0054 (Door service), 0056, 0060 (interface and role), 0063 (read
speed), 0069 (media by digest).

## enrich (Python)

Purpose: machine claims proposed through Door, derived media, and the feature
bank. Doc: [enrich/README.md](enrich/README.md) (875; one section per
command, `rg -n '^##' enrich/README.md`).

| File | Lines | What |
|---|---|---|
| `enrich/enrich.py` | 685 | `main` (619): `probe`, `detect`, `match`, `match-windows` (`run_probe`, `run_detect`, `run_match`, `run_match_windows`) |
| `enrich/adapter.py` | 588 | `propose`: a tool result to a Door proposal (ADR 0064) |
| `enrich/propose.py`, `proposers.py` | 266, 999 | `enrich propose`; `PROPOSERS` (registry at the end): bank features as claims, the bass line and vocal bleed filter (`bass_line`, `read_bass_line`, `drop_bleed`), lines built from `f0-pesto` (`build_line`), forced alignment declared and refused `not_yet` |
| `enrich/note_builder.py` | 356 | `build`: one note per held pitch from a pitch track and its level (ADR 0086); settings `VOICE`, `BASS`; tests `enrich/test_note_builder.py` |
| `enrich/probe.py`, `detector.py` | 234, 674 | stream facts (0065), span suggestions (0066; presets and the talk gate, 0091) |
| `enrich/match.py`, `cover.py`, `windows.py` | 358, 1073, 517 | work matching (0067, 0071, 0072) |
| `enrich/extract.py` | 756 | feature-bank runner: `main` (`list`, `run`), `run_default`, `run_from` |
| `enrich/bank.py` | 819 | `Catalog` (326), `Bank` (704): the bank format |
| `enrich/remote.py` | 494 | extractor runs through Spool: `put`, `work`, `collect` (ADR 0090) |
| `enrich/backfill.py` | 836 | `enrich backfill` (ADR 0112): `plan`, `Planner.state` (reads the bank through `Bank`), `layout`/`copies` (slots and threads), `Runner` (the pool and the Spool fan-out); tests `enrich/test_backfill.py` |
| `enrich/media.py`, `waveform.py`, `derived.py` | 193, 161, 62 | derived media (ADR 0069) |
| `enrich/support.py` | 291 | test support: synthetic audio, scratch archives |
| `enrich/extractors/<id>/` | | `extractor.json` + program; longest: `quality/quality.py` 1317, `tonal/tonal.py` 986, `structure/structure.py` 871, `rhythm/meter.py` 821 |

Tests: suite `enrich`, `enrich/test_<topic>.py` by command or extractor
family (`git ls-files 'enrich/test_*'`; `test_bank` 1147, `test_extractors`
runs all extractors together); suite `enrich-tests` is
`enrich/tests/test_media.py`.
The bench (`enrich/bench/`, [README](enrich/bench/README.md)) scores note
transcribers against private sets outside the repository; suites `bench` and
`bench-horn`. The `enrich` suite does not discover it (no `__init__.py`).
Rules: enrichment never changes its inputs (INV-9); every claim carries its
run and provenance (ADR 0064, [provenance](docs/provenance.md)); an
extractor's version changes whenever its outputs can (ADR 0074); stems are
content (ADR 0075); preferred inputs ADR 0076.
Bulk import (ADR 0112): `enrich backfill` is built over whole content;
decided, not built: stand-in stems by a measured tolerance (ADR 0090's
amendment), region entries, bank requests (`--follow`), and the cost gate;
the build work is step 0 of [docs/backlog.md](docs/backlog.md).
Pitfall: the backfill's job events are `ran`/`failed`; `done` is only its
last line.
Find: extractors in `enrich/extractors/<id>/extractor.json`; SHIPPED digest
pins in `enrich/test_bank.py` (`SHIPPED = {`, line 115); byte format in
[docs/feature-bank.md](docs/feature-bank.md) (444; shipped table at "The
shipped extractors").

## capture (Python)

Purpose: import a recorder card as sessions, then run probe/detect/match.
`capture/capture.py` (670): `main` (624, `import`), `read_directory`,
`group`, `plan`, `admit`, `enrich`; `grammar.py` (124) recorder file-name
grammars, `GRAMMARS`; `send.py` (185) sends a card read elsewhere.
Tests: suite `capture`; `test_capture` (763), `test_grammar`, `test_send`.
Rules: ADR 0070; capture proposes, Door commits (INV-2).

## migration (Python)

Purpose: read-only V2 census and the V2 audio route's Door proposals.
`v2_translate.py` (1218): `main` (`plan`, `verify`, `probe`); `v2_bundle.py`
(1009) frozen bundle (ADR 0036); `v2_lookup.py` (149); `census.py` (600);
`audio_route.py` (341, ADR 0040, 0047); `source_manifest.py` (310).
Tests: suite `migration`; `git ls-files 'migration/test_*'` (six modules).
Rules: translate V2 into this vocabulary; V2 never answers an open question
(ADR 0015); evidence and claims are preserved (ADR 0035); unit status in
[docs/port-ledger.md](docs/port-ledger.md).

## shadow and fixture (Python)

shadow: `compare.py` (509) manifests and census reports; `review.py` (429)
validates human checkpoint reviews; `scratch.py` (295) scratch archives (ADR
0061). Suite `shadow`: `test_compare`, `test_review`, `test_scratch`.
Procedure: [docs/shadow-runbook.md](docs/shadow-runbook.md) (424).

fixture: `build.py` (290, `main`) admits `plan.json` (413) through the real
Door; `serve.py` (96) builds and serves it with the relay (ADR 0062). Suite
`fixture`: `test_fixture`. The plan cites every contract in the current
catalog except the specification upgrade, so a new contract needs a plan step.

## Do not read

| Path | Instead |
|---|---|
| `spec/contracts/sha256-*.json` (one-line bundles) | `rg -l '"<contract id>"' spec/contracts`, or the catalog `spec/<v>/contracts.json` |
| `spec/0.0.*/archive-spec.md` (old versions) | read `spec/archive-spec.md` by heading; `git diff --no-index spec/0.0.8/archive-spec.md spec/0.0.9/archive-spec.md` for a change |
| `world/tests/fixtures/*.json` (one-line exports, up to 133 KB) | `python3 -c` with `json` to count or pick; regenerate via `door-test` with `ARCHIVE_TEST_WORLD_EXPORT` (world/README.md, Tests) |
| `fixture/content/*` (binary) | `fixture/README.md` says how each was made |
| `door/test/Main.hs` (7856), `RecordCommit.hs` (3015), `Contracts.hs` (2448) | `rg -n` the function or assert text, then read around it |
| `relay/openapi.json` (1935) | `python3 -c` with `json` on `["paths"]["/route"]` |
| `docs/port-ledger.md` (520) | `rg -n '<unit>' docs/port-ledger.md` |
| `flake.lock`, `dist-newstyle/`, `cabal.project.local` | not source; never edit by hand |
| SHIPPED digests in `enrich/test_bank.py` | let the test print the new digest |

## Slow things

- `enrich`: about 16 minutes, the longest; run one module with `-p`.
- `haskell`: about 4-7 minutes; monolithic, no filter.
- Others: no recorded figure; limits are the timeouts above. An unchanged
  tree is answered from cache.

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
   data-files; Door rules in `Contracts.hs`/`AdmissionDryRun.hs` with
   `door/test/Main.hs`; a step in `fixture/plan.json`; World projection. Model:
   commit `369a7d3` (spec 0.0.9). Governs: ADR 0057, 0068, 0027.
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
   version, never an edit). Admission flow: `AdmissionDryRun.hs`. A new
   refusal: `Refusal.hs` and `door/service.schema.json`. Tests in
   `door/test/Main.hs`. Governs: docs/invariants.md, ADR 0023, 0058.
7. **Add a transition kind.** `transitionDispatchTable` and the handler in
   `TransitionDryRun.hs`/`TransitionCommit.hs`. Governs: ADR 0028, 0030.
