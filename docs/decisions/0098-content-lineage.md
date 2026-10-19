# 0098. Content lineage: an Artifact can say what it was made from

Status: proposed (wave 1, drafted 2026-10-19)

A sample cut from a rehearsal, a bounce built from three such samples, a
stem admitted because someone sent it, a practice render kept as evidence:
each is an Artifact made from other content. Nothing in the archive can say
so.

- An Artifact's payload is only its content digest and size (D-8), and an
  Artifact pins nothing. Reach (`reach` in `world/documents.py`, and Door's
  `Archive.Door.Reach`) follows pins outward from an object, so a bounce
  built from a recording a bandmate marked for the operator alone reaches as
  far as the bounce's own claims allow, which is further than its source.
  Only a document's `document_source` claim adds an edge backward, from the
  document to what it was `derived_from` ([ADR 0085](0085-documents-lyrics-rights.md)).
- [ADR 0079](0079-machine-harmony-claims.md)'s `derived_from` relates Claim
  revisions only, and its bank citation names a stem without admitting it.
  What records where an admitted stem or render came from is
  [open](../open-questions.md#claims-and-provenance).
- [ADR 0089](0089-practice-audio-and-exports.md)'s export claim relates one
  exported file to the claim revisions it renders and the Artifacts it
  joins, and nothing else.
- Destroy needs a disposition for "every dependent artifact with content of
  its own that is reachable from the destroyed content under the installed
  contracts" ([ADR 0024](0024-retire-and-destroy-records.md), D-5). No
  installed contract defines that reachability, so Door refuses every
  `dependent_destroy` and `dependent_source_gone` reference, and "MUST NOT
  guess reachability" (D-9). Destroying a recording today leaves every
  sample cut from it a silent survivor.

The sample app (cuts made in the review app, browsed beside songbook songs
and organised libraries) and friends who admit files and cut make this urgent:
samples are the content that reach, destroy, and credits most need to
follow.

The operator decided the following on 2026-10-19 (wave 1 decisions W1.12 to
W1.14); this record states it as given.

- **W1.12 Content lineage.** A lineage claim says an output Artifact was
  made from inputs: Artifacts, selections, or bank stem citations. Optional
  placements: output span <- input span, with transforms (gain, fade, pitch,
  stretch, reverse, slice). Whole-output-only lineage ("this bounce uses
  that record somewhere") is allowed; partial knowledge is first-class.
  Because lineage only narrows reach, anyone with a cut grant may assert it;
  denying lineage would widen reach, so only the operator's judgment can
  reject a lineage claim. It also answers "where did an admitted stem or
  render come from" (open question; ADR 0079).
- **W1.13 Lineage restricts reach.** Reach of an output is capped by the
  strictest sensitivity and rights among its lineage sources, computed
  backward through lineage claims. An Artifact with neither lineage nor
  origin stays unknown origin (others' work beyond the circle, today's
  default). This only ever narrows, so INV-18 holds.
- **W1.14 Destroy follows lineage.** When a source is destroyed, everything
  whose lineage names it is a dependent needing a recorded disposition
  (`dependent_destroy` / `dependent_source_gone`, ADR 0024), never a silent
  survivor.

## Decision

### `archive.claim/lineage` version 1

A **lineage claim** says one output Artifact was made from one or more
inputs. It maps to PROV's `wasDerivedFrom`, used as a mapping only
([ADR 0012](0012-inherited-meaning.md)). Its references:

- exactly one `output`: the Artifact revision that was made;
- 1 to 64 `input`: distinct exact revisions, in the asserter's order, each
  an Artifact or an `archive.claim/audio_selection` Claim revision
  (version 2; a later version when a release installs one). A stem the
  output was made from is cited through the recording it was separated
  from (below);
- at most one `run`: a derivation run Claim revision, when a machine made
  the output ([ADR 0064](0064-derivation-runs.md)).

Its payload, every field optional:

```json
{
  "complete": false,
  "inputs": [
    {},
    {"stem": {"extractor": "stems", "key": "sha256:<entry key>",
              "output": "drums", "sha256": "sha256:<stem file digest>"}}
  ],
  "spans": [
    {"input": 1,
     "from": {"start_sample": 441000, "end_sample": 463050, "sample_rate": 44100},
     "to":   {"start_sample": 0, "end_sample": 44100, "sample_rate": 48000},
     "transforms": [{"op": "slice", "index": 3, "of": 16},
                    {"op": "stretch", "ratio_ppm": 1000000},
                    {"op": "pitch", "cents": -200},
                    {"op": "gain", "millibels": -600},
                    {"op": "fade", "edge": "out", "samples": 2400, "shape": "linear"}]}
  ]
}
```

- `inputs`, when present, has exactly one entry per `input` reference, in
  the same order. An entry is `{}` (the input itself) or `{"stem": …}`: the
  named `file` output of the bank entry, in exactly ADR 0079's citation
  shape, whose recording is that `input`, which must then be an Artifact.
  The stem is the recording ([ADR 0075](0075-stems-as-content.md),
  ADR 0089), so the pin is what reach and destroy follow; the citation says
  which signal was used. A stem that was itself admitted is cited as its
  own Artifact instead.
- `spans` are the decision's placements, called **lineage spans** here
  (see New nouns): 0 to 10,000 of them, each naming an `input` by its index
  and at least one of `from` (where in the input) and `to` (where in the
  output), each a half-open span of integer samples with its rate, as a
  selection's spans are (S-15). `from` counts the input's own samples: an
  Artifact's or a stem's (a stem shares its recording's timeline), or, for
  a selection, samples from the selection's start. An input with no span is
  used "somewhere"; no spans at all is whole-output-only lineage.
- `transforms`, on a span, is an ordered list of what was done between
  `from` and `to`, from a closed set, every number an integer so canonical
  JSON has one spelling: `gain` (`millibels`, -9600 to 2400), `fade`
  (`edge` `in` or `out`, `samples`, `shape` of `linear`, `equal_power`,
  `exponential`, or `unknown`), `pitch` (`cents`, -4800 to 4800), `stretch`
  (`ratio_ppm`, output length over input length in parts per million,
  100,000 to 10,000,000, pitch kept; a speed change that moves pitch is a
  stretch and a pitch), `reverse`, and `slice` (`index` from 0, optional
  `of`, the number of slices the input was cut into). An absent list says
  nothing about transforms; an empty list says there were none. Transforms
  describe; they are not a recipe a renderer must reproduce.
- `complete`: `true` when the asserter says these are all the inputs.
  Absent or `false` says nothing about other inputs. Partial knowledge is
  the default, not an error.
- `recipe` (not shown) says how a regenerable file that was chosen and
  admitted was made, answering the open question on admitted stems and
  renders: `{"bank": <citation>}` when the output's bytes are that bank file
  of the single input recording (the citation's `sha256` equals the output's
  digest, and its key names the separator and settings, ADR 0073), or
  `{"practice": <practice request>}` when the output is a practice render of
  the single input recording (ADR 0089's request, whose `content` is that
  input's digest and whose key World recomputes with `practice_key`). An
  admitted export has its own claim (below) and never a recipe here.

**Door rules** (a specification release adds them with the bundle): one
`output` Artifact; distinct inputs, none of them the output, and none with
the output's content digest; an `input` target that is an Artifact or an
installed selection version; `inputs` the same length as the `input`
references, with a stem only on an Artifact input; every span's `input` in
range and at least one of `from` and `to`, with `0 <= start_sample <
end_sample` and a positive rate; transforms from the closed set with their
ranges; a `bank` recipe's digest equal to the output's, with exactly one
input. The agent is any person, or a tool's Package URL with a `run`.

**Who may assert it.** Lineage only narrows reach, so a member whose login
has the cut grant ([ADR 0094](0094-peoples-writes.md)) may assert it over
what their audiences can read, like a cut; the relay adds the contract to
the member contracts under that grant, and its existing rule that every pin
be readable applies. A tool that admits a derived file (an admitted stem, a
chosen render, a sample render in wave 2) proposes the lineage claim in the
same batch as the Artifact, as a run's outputs travel with the run.

**Judging it.** A lineage claim is judged with `archive.assessment/judgment`
like any claim, and disagreement stands side by side (INV-7). For reach and
the views below, one judgment counts: a lineage claim revision is set aside
only while the active operator's standing on it (`standing` in
`world/judgments.py`) is `rejected`; a later operator `affirmed` restores it.
Anyone else's `rejected`, and the asserter's own `withdrawn`, are kept and
shown but set nothing aside, because each would widen reach. For the same
reason reach reads every revision of a lineage object, as it reads every
revision of any object today: a revision that drops an input widens nothing
until the operator rejects the revision that named it. The relay refuses a
`rejected` judgment of a lineage claim drafted in member capacity
(`operator_only`): a login through Cloudflare is member capacity, the
operator's own included ([ADR 0092](0092-internet-exposure.md), W1.3), and
the record alone cannot show the capacity it was made in.

### Reach is computed backward through lineage (W1.13)

**One edge, read where pins are read.** World's pin map (`_pins` in
`world/documents.py`) gains, for every lineage claim revision not set aside,
an edge from its `output` to each `input`. The closure of an Artifact then
contains everything it was made from, transitively, and what those inputs
pin: a selection's recordings, a stem input's recording, the inputs' own
lineage. The existing rule then applies unchanged: the strictest sensitivity
label in the closure wins, and every Artifact, Work, recording, and release
in it needs a rights claim permitting the class (INV-21). A sample cut from
a recording marked for the operator alone reaches the operator alone; a
sample whose source has a personal-use licence stays in the circle;
a bounce of three samples reaches no further than the strictest of them.
Door's `Archive.Door.Reach` index gains the same edge, so a publication
approval beyond the circle is refused by the same rule Door already shares
with `beyond_circle` (ADR 0085's amendment).

**Rights through a lineage edge ask for derivative use.** Where [ADR 0097](0097-rights-by-use.md)
lets a rights claim say what use it permits, a source reached through a
lineage edge is read for use in a derivative, and the beyond-circle check
reads a clearance for that use (ADR 0097); a
source reached through any other pin is read as today. Until ADR 0097's
contracts are installed, every source is read as today, which is never
wider.

**Two existing claims feed the same edge.** `document_source` already adds
document to `derived_from`. The export claim adds its `artifact` to each
`rendered` and `joined` revision, so an admitted export reaches no further
than the lead sheet it renders and the lyric document it joins, which
nothing enforces today.

**Origin is [ADR 0096](0096-origin-of-recordings.md)'s.** Lineage caps reach; it never stands in for
origin. An output with lineage and no origin claim keeps ADR 0096's unknown
origin, so beyond the circle it is still others' work needing a fresh
approval (INV-20). Whether a complete lineage may ever supply an origin is
question 1 below.

**Cost.** Building the pin map gains one pass over lineage claims and over
the operator's judgments of them: linear in records, done once per Reacher
as today. Each Artifact's closure grows by its ancestry. A Reacher computes
a closure per object without sharing, so a readable set over N objects
costs the sum of their closures; a library of cuts each made from one
recording adds one or two objects to each closure, and a bounce of bounces
adds its whole ancestry. Lineage can form cycles (A said to be made from B,
B from A, by two people); the closure's visited set already ends them, and
the members of a cycle share one reach, which is the safe answer. When
measured readable-set times demand it, the Reacher computes reach once per
strongly connected component of the pin graph, in reverse topological
order, keeping each limit's `why` line, which makes the readable set linear
in edges; nothing in this record depends on that optimisation.

### Destroy follows lineage (W1.14)

Installing the lineage contract defines the reachability D-5 asks for, so
a specification release replaces D-9's refusal with this rule. When a
destroy claim destroys content D, the **dependents** of D are the output
Artifacts of every committed lineage claim revision, judged or not, any of
whose `input` references pins an Artifact with content D, or an
`audio_selection` revision with a span pinning one. Each dependent whose
content is not D and was not destroyed earlier needs exactly one pinned
reference, pinned to its head revision: `dependent_destroy`, with that
content's own destroy claim (and the retire it needs) in the same atomic
batch, or `dependent_source_gone`, keeping it with its source's loss
recorded. Door refuses a missing, extra, or doubled disposition.

- **Direct, and recursive through the batch.** Only direct outputs are
  D's dependents. A `dependent_destroy` brings a destroy claim that has its
  own dependents, so a chain is followed exactly as far as the operator
  destroys it, and a `dependent_source_gone` survivor keeps its own
  derivatives untouched.
- **Rejected lineage still counts.** Door reads no judgment here: an extra
  disposition on a falsely claimed dependent costs one reference and keeps
  nothing from being destroyed, while ignoring a true one would leave the
  silent survivor W1.14 forbids. Both directions overcount: reach to stay
  narrow, destroy to stay complete.
- **The other lineage claims.** A `document_source` document `derived_from`
  D (a transcript of the destroyed recording) and an admitted export that
  `joined` D are dependents by the same rule (question 4).
- **Cost.** Door already replays the committed history; it builds one index
  from input object to lineage outputs in that pass, so a destroy checks its
  dependents in time linear in the lineage claims.

A disposition is a decision, not an erasure: `dependent_source_gone` keeps
the sample and records that its source is gone. What erasing information
derived from destroyed content means stays
[open](../open-questions.md#door) (ADR 0004).

### World and the relay

- **Projection.** World projects a lineage claim as `prov:wasDerivedFrom`
  from output to each input, the claim's spans and transforms as resources
  of their own under `arch:` terms, and a stem citation as ADR 0079's is
  projected. Lineage set aside by the operator is projected with its
  judgment, never dropped.
- **Views.** A `lineage` view answers an Artifact's sources, transitively,
  with spans, transforms, recipes, and each claim's standing; a `derived`
  view answers the reverse, what was made from an Artifact or a selection,
  which is what the sample app shows as "used in" and what the operator
  reads before a destroy. `reach`, `readable_set`, and `may_publish` change
  only through the pin map. Credits stay contributions
  ([ADR 0078](0078-contributions.md)); lineage is evidence a credits view may
  read later.
- **Relay.** The relay accepts lineage drafts from members with the cut
  grant, refuses a member-capacity rejection of one, and serves an output
  only within its reach, as it serves everything today; its readable set
  narrows by itself once the pin map has the edge.

### New nouns

- **Lineage claim** (content lineage). (1) It says what an Artifact was
  made from, which nothing else says of content; `derived_from` relates
  claims, a run records what a machine read, and a document's source covers
  documents only. (2) It owns no verb; it is asserted and judged like any
  claim. (3) It enforces none itself; it lets reach keep INV-21, INV-26,
  and INV-18 for derived content, and lets destroy keep INV-12. (4) It is
  code inside an existing concept: a claim contract, read by World's reach
  and Door's destroy check. (5) Its claims are evidence; its effects on
  reach and dependents are computed, never stored. (6) A claim contract,
  not a subsystem.
- **Lineage span** (the decision's placement). (1) It says where in the
  output a part of an input landed, which no selection or alignment says
  across two pieces of content. (2) No verb. (3) None. (4) It is a field of
  the lineage claim. (5) No. (6) A detail of the contract. The word
  *placement* already names where managed copies are placed
  ([ADR 0032](0032-managed-placement-over-ssh.md)), so this record uses
  *lineage span*.
- **Transform.** (1) It names, in a closed list, what was done to a span
  between input and output. (2) No verb; it is not applied by Archive.
  (3) None. (4) It is a field of a lineage span. (5) No. (6) A detail.
- **Dependent** is ADR 0024's word, now given a reachability; no new noun.

## Consequences

- Cutting a sample and saying where it came from caps its reach at its
  source's, whoever cut it. Saying nothing leaves the sample's reach where
  its own claims put it, and its origin unknown (ADR 0096), which beyond the
  circle is already others' work.
- A member can narrow the reach of anything they can read by naming a
  restricted input, including the operator's own recordings. That is the
  decision's asymmetry: the operator rejects the claim, and until then the
  content stays closer, never further.
- Destroying a recording now lists every sample, bounce, admitted stem, and
  render made from it, and the operator decides each; nothing derived
  survives without a record saying so. The first destroy with dependents
  needs the specification release that installs the contract.
- An admitted stem or render says exactly how it was made (`recipe`), so it
  can be checked against, or remade from, the bank.
- The readable set grows costlier with ancestry depth. For a library of
  single-source cuts it is a small constant per object; deep bounce chains
  are where to measure first.
- No invariant changes. INV-18 holds because every edge added narrows;
  INV-7 holds because a rejected lineage claim is kept and shown.

## Alternatives considered

- **Lineage in the Artifact's payload.** D-8 closes the payload to content,
  and lineage is a claim people may disagree about.
- **Extending `derived_from` to Artifacts.** Its meaning is fixed as claims
  read from claims, with no spans, and its contracts are many; one contract
  for content keeps reach and destroy reading one shape.
- **Superseding the export claim with lineage.** An export's inputs are
  exact Claim revisions and an exporter key, which W1.12's inputs
  (Artifacts, selections, stems) do not include, and its key makes it
  reproducible. It sits beside lineage, and World and Door read it as a
  lineage edge for reach and (for `joined`) destroy.
- **Forward reach** (a source pushing its limits onto what pins it). It
  would make the source's record name its outputs, which do not exist when
  it is admitted; reach computed from the output backward needs no change
  to old records.
- **Any judge's rejection setting lineage aside.** A rejection widens
  reach; W1.12 gives that only to the operator.
- **Honouring rejections at destroy.** Door would need the operator's
  standing to decide completeness, and the only gain is one fewer
  reference.
- **A transform recipe precise enough to render from.** That is wave 2's
  sample (a selection version 3 with a transform recipe); lineage records
  what is known of any file, including one made outside the archive.

## Touch points

- `world/documents.py`: `_pins` (the output-to-input edge, the export
  edge, operator-rejected revisions set aside), `Reacher`, `reach`, and a
  new `check_lineage`.
- `world/judgments.py`: `standing`, read by the pin map for the operator.
- `world/harmony.py`: `check_stem_citation`, reused for `inputs[].stem`.
- `world/exports.py`: `check_practice_request` and `practice_key`, for a
  `practice` recipe; `check_export`, unchanged, read by the pin map.
- `world/circle.py`: `MEMBER_CONTRACTS` and `check_member_draft` (lineage
  under the cut grant; a member's rejection of lineage refused),
  `readable_set` and `beyond_circle` (unchanged, through the Reacher).
- `world/contract_graphs.py` and `world/projection/6/vocabulary.ttl`: the
  lineage graph and its `arch:` terms.
- `world/record_views.py` and `world/views.py`: `VIEWS` gains `lineage`
  and `derived`.
- `relay/access.py`: the draft check call site, and the operator-only
  refusal.
- `door/src/Archive/Door/Reach.hs`: `indexHeld` and `closure` gain the edge
  and read the operator's judgments.
- `door/src/Archive/Door/TransitionDryRun.hs`: `validateDependents` replaces
  its refusal with the dependent computation; `validatePublicationReach`
  follows `Reach.hs`.
- `spec/archive-spec.md`: D-5 (reachability) and the D-9 paragraph ending
  "MUST NOT guess reachability", plus the contract bundle and its profile
  rules; `docs/vocabulary.md`: *lineage claim*, *lineage span*,
  *transform*, and *reach* ("everything it pins or was made from").

## Open for the operator

1. **Can lineage ever supply origin?** W1.13 says an Artifact with
   "neither lineage nor origin" stays unknown origin, which reads as if
   lineage alone were enough; but treating a member's lineage as origin
   would let it widen (an unknown-origin bounce becoming the operator's own
   work because its named sources are). Recommend: lineage never stands in
   for origin, except a `complete: true` lineage claim the operator asserted
   or affirmed, which lets the output take the most restrictive origin of
   its sources (ADR 0096 to state it; as drafted, ADR 0096 leaves this
   question to this record).
2. **The word.** Recommend *lineage span* for the decision's placement,
   since *placement* names managed copies (ADR 0032).
3. **Withdrawal and revision.** Recommend that neither the asserter's
   `withdrawn` nor a revision that drops an input widens reach; only the
   operator's `rejected` of the exact revision does.
4. **Which existing claims are lineage for destroy.** Recommend
   `document_source` `derived_from` and export `joined` count (so a
   transcript is a dependent of its recording, which surfaces the erasure
   question per file without answering it), and export `rendered` does not
   (claims carry no bytes; it counts for reach only).
5. **Capacity in the record.** World cannot tell an operator judgment made
   over the tailnet from one made through Cloudflare. Recommend the relay
   refuse member-capacity rejections of lineage now, and ADR 0092 or 0094
   decide whether transitions and operator judgments should record capacity
   (ADR 0092, its question 3, recommends not recording it for now).
6. **Inputs that are not in the archive** (a commercial record sampled but
   never admitted). Recommend: not expressible as lineage; the output's
   origin claim (ADR 0096, `commercial_release`) carries it until the
   source is admitted.
7. **Required lineage.** Recommend that Archive's own tools that admit a
   derived file (stem, render, sample render) must propose its lineage in
   the same batch, and that what people admit through the relay is never required to.
8. **Cycles.** Recommend Door refuse only self-lineage (output among
   inputs, by object or digest) and World tolerate longer cycles, giving
   every member of one the same, strictest reach.
