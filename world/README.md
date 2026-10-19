# World v1 prototype

World builds a disposable RDF view from one complete `archive export` result.
The export pins a Git commit and carries the committed context, every
installed contract bundle, and raw record revisions. World does not read archive worktree files
or write to the archive. Its store is regenerable; `CURRENT` selects an
immutable generation built from one snapshot and projection version.

## Chords and meter

[`harmony.py`](harmony.py) holds the rules of the three contracts of
[ADR 0077](../docs/decisions/0077-chord-stacks-and-meter.md),
`archive.claim/chord_stacks`, `archive.claim/chord_sequence`, and
`archive.claim/meter_layers`, the names
computed from a chord stack (Harte strings and lead-sheet symbols), the
tempo derived from bars, and lossy JAMS views. It needs only the standard
library. Specification 0.0.9 installs the contracts (D-17): Door admits them,
and World re-checks each claim with this module before it projects it
(projection 6, revision 5). Its tests run alone:

```sh
python3 -m unittest world.tests.test_harmony
```

## Credits

[`contributions.py`](contributions.py) holds the rules of the two contracts of
[ADR 0078](../docs/decisions/0078-contributions.md),
`archive.claim/contribution` and `archive.claim/band_membership`, the
MusicBrainz relationship types each scope accepts and the attributes and
vocals they take (each with its MusicBrainz identifier), the resolved view in
which a finer credit refines a coarser one, and the join from a chord stacks
claim to whose part it is. It needs only the standard library. Specification
0.0.9 installs the contracts (D-17): Door admits them, and World re-checks
each claim with this module before it projects it. No importer proposes
them yet ([contribution sources](../docs/contribution-sources.md) is a
design). Its tests run alone:

```sh
python3 -m unittest world.tests.test_contributions
```

## Specification 0.0.10

These modules hold the rules of the 0.0.10 records. Specification 0.0.10
installs their contracts (D-18), and World re-checks each claim with its
module before it projects it ([`contract_graphs.py`](contract_graphs.py),
projection 6, revision 6; revision 7 adds the amendments of 2026-10-19:
syllables and the notes that carry them, articulation, the bass line, and
publication approval and withdrawal version 2). Each module needs only the
standard library.

| Module | Record | Holds |
|---|---|---|
| [`harmony.py`](harmony.py) | [ADR 0079](../docs/decisions/0079-machine-harmony-claims.md) | chord stacks and chord sequence version 2, the bank citation of a stem, `derived_from` |
| [`tonality.py`](tonality.py) | [ADR 0080](../docs/decisions/0080-key-and-tuning.md) | key sequences, key regions, tuning, spelling by key |
| [`judgments.py`](judgments.py) | [ADR 0081](../docs/decisions/0081-judgments-and-annotations.md) | judgment version 2, standing, span annotations |
| [`views.py`](views.py) | [ADR 0082](../docs/decisions/0082-views-and-current.md) | the named views and the first current rule |
| [`form.py`](form.py) | [ADR 0083](../docs/decisions/0083-form-and-work-meter.md) | form, form spans, meter layers version 2 (with ADR 0079's additions) |
| [`lead_sheets.py`](lead_sheets.py) | [ADR 0084](../docs/decisions/0084-lead-sheets.md) | lead sheets, changes since, a render's export request |
| [`documents.py`](documents.py) | [ADR 0085](../docs/decisions/0085-documents-lyrics-rights.md) | document sources, timed lyrics and their syllables, sensitivity, rights, reach (`Reacher`, read once per set of records), staging checks |
| [`notes.py`](notes.py) | [ADR 0086](../docs/decisions/0086-notes-and-melody.md) | notes, melody, melody spans, articulation, the bass line, written lengths and written rhythm, instrument fields |
| [`catalogue.py`](catalogue.py) | [ADR 0087](../docs/decisions/0087-tracklists-and-correspondence.md) | tracklists, recording audio, catalogue correspondence, credits per track |
| [`circle.py`](circle.py) | [ADR 0088](../docs/decisions/0088-bandmates.md) | the relay's circle, the readable set, work origin, sharing consent and gate, publication approval and withdrawal version 2, the check beyond the circle, `may_publish` |
| [`exports.py`](exports.py) | [ADR 0089](../docs/decisions/0089-practice-audio-and-exports.md) | practice requests and keys, transposition, export requests, keys and claims, MusicXML `<harmony>` |

Their tests run with the rest: `python3 -m unittest discover -s world/tests`.

[`contract_graphs.py`](contract_graphs.py) holds the table of which module
re-checks which contract, with what it reads (the stream, sequence, form,
tuning, or notes a claim pins, the agent's Entity kind, the active
operator), and projects each checked claim into its own graph by its
contract's context: a field is the predicate its context names, an object a
node `<{revision}:{field}>`, an array's item `<{revision}:{item}={n}>` with
its position as `rdf:value`, and a reference the predicate its role names.
It lists where World departs from that reading, and why: fields the context
leaves to the core context (`kind`, `label`, `role`, `at`, `name`) and terms
earlier shapes already target get World's own terms; an index into a form's
sections, a sequence's entries, a meter's bars, or a key sequence's regions
names that node, in this claim's graph or in the pinned claim's; a rights
statement is its concept of D-18's rights scheme, which the domain graph
holds; and a grouping, a spelling, or open strings stay one text in order. A
claim of a contract new in 0.0.10 is typed `arch:Claim` in its own graph;
the versions 2 of chord stacks, chord sequence, meter layers, and judgment
keep their versions 1's shapes. A claim a module refuses fails the
projection, naming why; nothing is projected in part.

Until Door enforces 0.0.10's profile rules the fixture archive cannot hold
these claims, so World's tests draft them as `fixture/build.py` would
([`tests/plan0010.py`](tests/plan0010.py), from
[`tests/fixtures/plan-0010.json`](tests/fixtures/plan-0010.json) after the
fixture plan) and project the drafts. Once Door admits them, the fixture
plan holds them and the drafts can go.

## Named views

A named view is a question World answers by a stated rule
([ADR 0082](../docs/decisions/0082-views-and-current.md)), computed from the
selected generation when asked and never stored. [`views.py`](views.py) holds
the registry, the request rules ([`view_requests.py`](view_requests.py)), and
the chord views; [`record_views.py`](record_views.py) adds the views of
0.0.10's other records to the same registry. Every view is version 1:

| View | Subject | Answer |
|---|---|---|
| `chord_names` | `readings` | `names` (per reading: `form`, `resolutions` of `tones` and `names`), `spelling` |
| `chord_corrections` | `source` | `segments` of `shown`, `corrected`, `conflict` |
| `chord_differences` | `source` | `differences` (`kind`, `label`, both sides, `only_in_a`, `only_in_b`, basses), `not_compared` |
| `chords_current` | `source` | `rule`, `segments` (`chosen`, `stack`, `names`, `held`, `bass_from`, or `conflict`; `why`, `others`; `spelling` with a key), `unresolved_runs` |
| `lead_sheet_current` | `work`, optional `recording` | `by`, `chosen` or `conflict`, `others`, a band's `members` |
| `standing` | `claim` (a pin) | `verdict`, `conflict`, `judgments`, `why` |
| `reach` | `object` | `labelled`, `rights`, `furthest`, `why` |
| `readable_set` | `circle` (`destination`, `members`, optional `band`) | `revisions`, `withheld`, `why`; with a `reader`, `readable` |
| `lead_sheet_changes` | `lead_sheet` (a pin) | `changes` (`role`, `pinned`, `kind`, `by`, `verdict`) |
| `played_sections` | `form` (a pin) | `played` (`section`, `label`, `kind`) |
| `played_bars` | `meter` (a pin) | `form`, `played` (`bar`, `section`, `numerator`, `denominator`) |
| `tune` | `melody_spans` (a pin) | `notes` (each with `from`), `handoffs`, `left_out` (a machine's spans on the bass line) |
| `track_credits` | `recording` (an Entity) | `credits` (`claim`, `scope`, `via`, `why`, `refined_by`) |
| `written_rhythm` | `notes` or `melody_spans` (a pin) | `rule`, `written` (chords and rests: `position`, `duration`, `bar`, `at`; a chord's `notes`, `pitches`, `sounded_share`, `articulation`; a rest's `boundary`), `left_out`, or `why` |
| `bass_line` | `source` (the recording's Artifact) | `lines`: each head notes claim saying `bass`, with `clef` and its written rhythm |
| `may_publish` | `approval` (a pin) | `may_publish`, `blockers`, `class`, `destination`, `on` |

`chord_names` and `chords_current` take `transpose` (ADR 0089) and `key`: a
key reading for `chord_names`; for `chords_current`, `{"reading": …}` or
`{"claim": <key regions pin>}`, whose region over each stretch spells it
(ADR 0080 names no current key, so the reader chooses). Absent, World's
default spelling names the chord, as before. `standing` takes `judge` (the
caller's person by default), `at`, and `reading`; `reach` and
`readable_set` take `on`, the date terms are read on (today by default);
`played_bars` takes `layer` (0). `may_publish` takes `on`: a publisher asks
it when it publishes, since rights expire and consents change (ADR 0085,
amended); it applies `circle.beyond_circle`, the rule Door applies to an
approval beyond the circle on the day it is made. `written_rhythm` and
`bass_line` take `meter` (a timed meter claim's pin; without one nothing is
written and `why` says so) and `layer` (0); `written_rhythm` also takes
`lyrics`, up to 8 timed lyrics pins whose line ends and words mark phrase
boundaries. Its rule (ADR 0086, amended) is named by its version
(`notes.RHYTHM_RULES`): onsets on a grid of four points a beat, each note
filling to the next onset, `staccato` under 60% sounded, and a rest only
for a silence of a beat or more at a `line_end` or `breath`.

```sh
world/world.py view --list
world/world.py view --store /path/to/world-store --request-file request.json [--caller <person>]
```

answers `{"status": "ok", "result": {snapshot, projection, view, version,
subject, parameters, answer, withheld}}`, with every default filled in
`parameters`. A refused request exits 4 with `{"status": "refused", "code",
"detail"}`, the code the relay answers (`view_unknown`, `view_not_served`,
`view_request_invalid`, `view_subject_not_found`).
[`view_records.py`](view_records.py) reads the records a view takes back
from the records graph, exactly as committed, and narrows them to a
caller's readable set when the relay gives one, stating how many it
withheld.

## Install

Use a virtual environment and the dependency pinned to the initial Oxigraph
minor release:

```sh
python3 -m venv .venv-world
. .venv-world/bin/activate
python -m pip install -r world/requirements.txt
```

## Rebuild and query

The archive command must be available on `PATH`:

```sh
world/world.py rebuild /path/to/archive --store /path/to/world-store
world/world.py query --store /path/to/world-store --query-file question.sparql [--projection N]
```

A query answers in the W3C formats: SPARQL 1.1 Query Results JSON for SELECT
and ASK; for CONSTRUCT and DESCRIBE, N-Triples by default, or Turtle or
JSON-LD with `--rdf-format`. World's data is in named
graphs; the default graph is empty, so name the graphs (`GRAPH ?g { … }`). A
query using `SERVICE` is refused: World answers only from its own store.

### Patterns that use the indexes

`{archive}` is the archive's identifier, which the relay's `/health` answers.
Every claim and assessment revision has its own graph, named by the revision,
and its node in the records graph has its shape as `rdf:type`. Reach claim
graphs through that node; a filter on graph names reads every quad in the
store first.

```sparql
PREFIX a: <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#>
SELECT ?g ?s ?p ?o WHERE {
  GRAPH <{archive}#world:records> { VALUES ?t { a:Claim a:Assessment } ?g a ?t }
  GRAPH ?g { ?s ?p ?o } }
```

Each record links to the contracts it cites with `dcterms:conformsTo`. Select
by contract through that link, not by matching the `a:contracts` JSON text:

```sparql
PREFIX dcterms: <http://purl.org/dc/terms/>
SELECT ?g ?s ?p ?o WHERE {
  GRAPH <{archive}#world:context> { ?contract dcterms:identifier "archive.assessment/judgment" }
  GRAPH <{archive}#world:records> { ?g dcterms:conformsTo ?contract }
  GRAPH ?g { ?s ?p ?o } }
```

Add `?contract a:version "1"` to the first graph pattern to pin a version. To
leave a contract out, use `FILTER NOT EXISTS` around the link. Both patterns
are checked against the text matches they replace in World's tests.

### Runs, stream facts, and suggested spans

A machine claim names the derivation run that made it with
`prov:wasGeneratedBy`, in the records graph and in its own graph. To list the
suggested spans of a recording, start from the recording's object, reach each
suggestions claim through the records graph's `prov:wasDerivedFrom` link, and
read the spans in that claim's graph
([`examples/suggested-spans-of-recording.sparql`](examples/suggested-spans-of-recording.sparql)):

```sparql
PREFIX a: <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
SELECT ?suggestions ?position ?start ?end ?class ?rate ?run WHERE {
  GRAPH <{archive}#world:records> {
    ?recording a:object_id <{recording}> .
    ?suggestions prov:wasDerivedFrom ?recording ; a:measured_stream ?stream }
  GRAPH ?suggestions {
    ?suggestions a:sample_rate ?rate ; prov:wasGeneratedBy ?run ; a:suggested_spans ?span .
    ?span rdf:value ?position ; a:start_sample ?start ; a:end_sample ?end ; a:span_class ?class } }
ORDER BY ?suggestions ?position
```

The duration of an Artifact comes from its audio stream claims: each hangs a
stream node from the Artifact revision it describes, and the duration in
seconds is `sample_count / sample_rate`, an exact `xsd:decimal`
([`examples/recording-duration.sparql`](examples/recording-duration.sparql)):

```sparql
PREFIX a: <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#>
PREFIX prov: <http://www.w3.org/ns/prov#>
SELECT ?recording ?samples ?rate ?seconds ?channels ?claim ?run WHERE {
  GRAPH <{archive}#world:records> { ?recording a:object_id <{recording}> ; a a:Artifact }
  GRAPH ?claim {
    ?recording a:audio_stream ?stream .
    ?stream a:sample_count ?samples ; a:sample_rate ?rate ; a:channel_count ?channels ;
            prov:wasGeneratedBy ?run }
  BIND(?samples / ?rate AS ?seconds) }
```

Two runs that disagree give two rows, each with its claim and run; World
chooses neither. A run's own graph types it `prov:Activity`, with its method
(`a:method`, a node with `dcterms:identifier` and `a:version`),
`prov:startedAtTime`, `prov:endedAtTime`, each input as `prov:used`, its
`a:parameters` Artifact, and each reported program as a `prov:SoftwareAgent`
it `prov:wasAssociatedWith` (`foaf:name`, `a:version`, and its position as
`rdf:value`).

To use another `archive` executable, pass `--archive-command /path/to/archive`.
For a captured export, pass `--export-file export.json`; the positional archive
root is still required so World can enforce that the store lives outside it.
`world build export.json --output projection.nq` writes deterministic N-Quads
for inspection without opening a persistent store.

## Refresh

```sh
world/world.py refresh /path/to/archive --store /path/to/world-store
```

`refresh` extends the selected generation when it was built by the same
projection version for the same archive, under the same core context, from an
ancestor of HEAD: it asks `archive export --since <snapshot>` for the records
added since, copies the generation (a RocksDB checkpoint, mostly hard links),
projects only those records into the copy, and selects it
([ADR 0055](../docs/decisions/0055-incremental-world-refresh.md)). In any other
case it rebuilds in full. It reports `incremental`, `full` (with the reason),
or `unchanged`; a failure leaves the previous generation selected. Deleting
the store is always safe.

The records, context, and domain graphs are archive-wide
(`<archive>#world:records`, `#world:context`, `#world:domain`); each
generation's metadata records the snapshot it projects. Each immutable revision
remains a PROV entity; claims and assessments also remain Web Annotation
annotations. Archive's local JSON-LD context and the record's cited contract
contexts supply the field and reference terms. Claims, exact reference pins,
writer, asserting agent, timestamp, and payload remain queryable. Audio
selection pieces expose their ordered sample bounds and exact Artifact pins.
Song occurrences point to exact selection Claim revisions, and version 2
performance-to-Work claims point from an exact occurrence revision to an exact
Work revision. These domain links are scoped to each asserting Claim graph.
Version 1 performance-to-Work claims remain structural assertions; they do
not establish a song occurrence. Song occurrence version 2 and
`performance_work` version 3 project like their predecessors, for
multi-source selections. Specification 0.0.5 selections expose each
source track and its spans; capture alignments expose their anchors; titles,
kinds (as SKOS concepts), ordered artist credits, and session parts stay in
the graph of the claim that asserted them; and a judgment names the exact
claim revision it judged, with its verdict. Competing titles or kinds appear
side by side. Migration translation records remain in the records graph with
their pins. Specification 0.0.7's machine claims (revision 3 of projection 6)
stay in their own graphs too: a derivation run as a `prov:Activity`; an audio
stream's facts on the Artifact revision they describe; span suggestions as
ordered span nodes with their class, measured stream, and run; a version 2
work match with what it compared (an occurrence as `arch:performance`, or a
selection), its Work, corpus, decimal score, method, parameters, and run; and
a version 2 alignment as version 1 projects. An `artifact` or `source`
reference keeps its reified statement in the records graph but no direct
triple, because those roles are core terms naming `arch:Artifact` and
`arch:source` (see the [open questions](../docs/open-questions.md)).
Specification 0.0.8's window matches (revision 4) name the span suggestions
they cut a window from with `arch:compared_suggestions`, and the window as a
node, `<{revision}:window>`, with `arch:start_sample`, `arch:end_sample`, and
`arch:sample_rate`; the window always starts and ends on suggested span edges
and holds whole spans, so a client can draw it over the spans it came from. Their Work, corpus, score,
method, parameters, and run project as a version 2 match's do.
Specification 0.0.9's claims (revision 5) are re-checked by `harmony.py` or
`contributions.py` before they project. A chord stacks claim's spans are
nodes `<{revision}:span={n}>` with ranked readings `<…:reading={m}>`, each
span's `arch:span_end` (`asserted` or `held`) and, when placed from a chord
sequence, its `arch:sequence_entry`, the sequence's `<{sequence}:entry={n}>`;
corrections name what they correct with `arch:corrects`, and the corrected
claim's graph is untouched. A chord sequence's entries have no samples. A
meter claim's layers hold bars or beat samples; a link's
`arch:modulation_kind` is `equation` or `regrouping`, and tempo is never
projected. A contribution qualifies its subject with a `prov:Attribution`
whose `prov:hadRole` is a concept of the specification's role scheme, and a
band membership is an `org:Membership`. Chord names are views and are not
projected. World answers no generic `current`; a named current rule is
asked for with `world view` (below).

Specification 0.0.10's claims (revisions 6 and 7) are re-checked by their modules
and projected by `contract_graphs.py`, as the section above says; the
shapes of [`projection/6/`](projection/6/projection.json) declare each
field, and the domain graph holds D-18's rights scheme.

## What World emits

[`projection/6/`](projection/6/projection.json) declares what projection
version 6 emits: its named graphs, a closed SHACL shapes file for each, and
the Archive vocabulary they use. `world/conformance.py --store <store>` (or
`--export <file>`) checks a projection against it; it needs
`world/requirements-test.txt`. Additions keep a projection's version; renames
and removals make a new version, served beside the previous one for at least
90 days. A store keeps `CURRENT-p<N>` for each served version and `CURRENT` for
the newest; `refresh` and `rebuild` build every served version
([ADR 0059](../docs/decisions/0059-world-read-contract.md)). The context graph
states the projection's version and the archive's specification:
`<{archive}#world> arch:projection_version 6`.

## Unsupported behavior

`world current` and `world promote` return `status: unsupported`. No per-kind
current rule exists, so World presents all supported performance-to-work and
work-to-MusicBrainz claims, including competing mappings, with their exact
claim revisions and attribution. It does not select a winner. See the accepted
[work-correspondence decision](../docs/decisions/0042-work-correspondence.md)
and the SPARQL examples in `world/examples/`.

`world musicbrainz-work <MBID>` performs one targeted lookup of a MusicBrainz
Work. A successful result is labeled `candidate`; network, not-found, and bad
response results are labeled `unavailable`. The result is disposable and is
never admitted to Archive or promoted automatically. A person's judgment can
support a correspondence claim; any fetched response used as evidence must
first be admitted and pinned separately.

An incomplete or unresolved export fails with `status: incomplete`; an
unsupported shape, context term, or requested feature fails with
`status: unsupported`. Neither result is an empty graph or a successful
zero-match answer.

## Tests

```sh
python -m pip install -r world/requirements-test.txt
ARCHIVE_TEST_DOOR=$(cabal list-bin door) ARCHIVE_TEST_ARCHIVE=$(cabal list-bin archive) \
  python -m unittest discover -s world/tests -v
```

The conformance tests build the [fixture archive](../fixture/README.md)
through the real Door, so they need the two variables; without them they are
skipped, unless `ARCHIVE_REQUIRE_INTEGRATION` is set, when they fail. They
require the fixture to cite every contract in the specification's catalog
except the specification upgrade, which an archive born at the current
release cannot cite, every served projection of it to conform, and every
declared term to appear in it. The upgrade tests (`test_upgrade.py`) upgrade
an archive at the previous release with the real `door transition` and
require World's next refresh to rebuild in full, equal a fresh projection,
and conform. The
derivation tests (`test_derivation.py`) answer the recording examples
against the same archive with golden rows, check its stream claim against
the synthetic recording's bytes, refresh incrementally across its runs, and
refuse each malformed run, stream, suggestion, and match.

The bootstrap export fixture was captured from a synthetic archive created by
`archive init` and emitted by `archive export`. The Door chain fixture is the
export of the archive built by Door's World admission test; regenerate it by
running `cabal test door-test` with `ARCHIVE_TEST_WORLD_EXPORT` set to its
path. Every example query except the two recording examples must return its
expected rows against that fixture; the recording examples are answered by
the fixture archive, since the Door chain holds no derivation runs.
The same run captures the export at a cut before the review records
(`ARCHIVE_TEST_WORLD_CUT_EXPORT`, `door-world-chain-cut-export.json`) and the
export since that cut (`ARCHIVE_TEST_WORLD_SINCE_EXPORT`,
`door-world-chain-since-export.json`); regenerate all three together. The
refresh tests require an incremental generation built from them to equal a
full rebuild, quad for quad and row for row on every example query.
The remaining fixtures exercise
claims, assessments, audio selections and occurrences, performance links,
competing MusicBrainz correspondences,
conflicting contract contexts, deterministic projection, and unresolved
references. MusicBrainz lookup behavior is tested without making network calls.
