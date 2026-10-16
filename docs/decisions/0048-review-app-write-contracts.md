# 0048. Review application write contracts

Status: accepted

The rehearsal review application is to replace the V1 review workbench
(ADR 0041). Every canonical write it makes must become a Door proposal under
an installed contract (ADR 0002). The installed contracts cover only part of
that: accepting boundaries is an `archive.claim/audio_selection`, asserting a
rendition is an `archive.claim/song_occurrence`, and linking it to a Work is
`archive.claim/performance_work` version 2 (ADRs 0044, 0045). The V1
workbench also names songs, edits title, kind, and artists, retracts,
publishes, and names sessions. None of these has a contract.

The operator chose on 2026-10-14 to identify songs as musical Works, to
record retraction as an assessment, to use one contract per fact, and to cover
publication and session naming in the same release.

## Decision

**A song is a musical Work.** The distinct song entity of ADR 0040 is an
Entity of kind `musical_work`, the kind World already projects and links to
MusicBrainz (ADR 0042). `archive.audio/song_entity` constrains a core Entity
to kind `musical_work` instead of introducing a `song` kind. The review app's
first identification keeps ADR 0040's mechanism: a random UUIDv4 `song_key`,
the identity name `song-<uuid>` in namespace `archive.audio/song`, and reuse
only through a human-reviewed reference. This supersedes ADR 0046's statement
that the two entities stay separate until a mapping is accepted, and ADR
0040's `song` kind. Title and score remain evidence, not identity.

**Retraction is an assessment.** `archive.assessment/judgment` version 1 is an
assessment. Its `assessment_of` reference pins one exact Claim revision of any
contract. Its payload is a `verdict` of `affirmed`, `rejected`, or `withdrawn`,
plus an optional `reason` of at most 2,000 characters. `withdrawn` is an
asserter taking back their own claim; `rejected` is a reviewer judging it
false. Nothing judged is rewritten (ADR 0005, INV-7). This also gives ADR
0047's retracted V2 rows a future home, through a later accepted rule.

**A selection covers every simultaneous source.** A session may be captured
by several microphones and direct line inputs at once. One accepted excerpt
must name all of them, so facts about it are stated once.
`archive.claim/audio_selection` version 2 replaces the flat `pieces` list with
`tracks`. Each track is one source's ordered, half-open spans, with sequential
artifacts such as a recorder rollover in one track (ADR 0044). All tracks
denote the same moment. Each span still pins its exact Artifact revision and
uses integer samples with that artifact's own rate. Door refuses two tracks
that pin the same content digest: bit-identical recordings are one content
item, not two views. A track's spans are aligned with another track's by an
`archive.claim/capture_alignment` claim (below), and a selection may pin the
alignment claim revisions it relied on, so a later dispute finds every
selection that depends on it.

**Alignment models clock drift.** Independent recorders and mixers run on
separate clocks. Their offset grows over a session, and the rate of growth
changes with temperature. A single offset is therefore insufficient.
`archive.claim/capture_alignment` version 1 pins a `reference` and an
`aligned` Artifact revision, with each one's nominal sample rate. It gives one
or more anchors, each pairing a reference sample with the aligned sample
recorded at the same instant. The anchors strictly increase in both
coordinates. Between consecutive anchors the mapping is linear. Beyond the
outer anchors it extends with the nearest segment's slope, or, with a single
anchor, at the nominal rate ratio. One anchor states a pure offset. Two state
an offset plus constant drift. More anchors follow drift that changes. The
deviation of each segment's slope from the nominal ratio is the measured drift.
Anchors are integers, so the claim is exact; a consumer that needs one sample
rounds half-even, as in ADR 0047. The claim also states `max_error_samples`,
the method's residual bound, and its method, and may pin the run that measured
it (ADR 0006). An operator's manual alignment is the same claim with a manual
method. Competing alignments coexist like any other claims (INV-7).
Version 1 selections stay valid, as single-track selections. A new source
joins an existing excerpt through a new revision of the same selection,
because it is still the same intended excerpt (ADR 0044).

**One contract per fact, about the selection.** V1 records a kind for every
span, including talk and transitions, which are not song renditions. These
facts therefore pin an exact `audio_selection` revision in role `selection`,
not an occurrence. Because a version 2 selection already includes every
source, no fact is repeated per microphone:

- `archive.claim/selection_kind` version 1: payload `kind` is one of
  `original`, `cover`, `improvisation`, `noodle`, `talking`, `transition`,
  `incomplete`, or `unknown`. These are V1's closed `PERFORMANCE_KINDS`,
  published as a SKOS concept scheme (P-11).
- `archive.claim/selection_title` version 1: payload `title` is a string of
  1 to 200 characters. A title never identifies a Work (ADR 0042).
- `archive.claim/artist_credit` version 1: one or more `artist` references,
  in credit order, each pinning an Entity revision of kind `person` or
  `organisation` (P-12). The order is meaningful, and duplicates are refused.
  Free-text names become Entities that the app proposes in the same batch.

**Publication follows ADR 0019.** `archive.transition/publication_approval`
version 1 pins an `authorizer` person Entity revision and one or more
`covers` revisions. Its payload names one `destination`, an operator-named
exposure class (ADR 0016), and a `decision` of `approve` or `hold_back`.
V1's absent `publish` value is not recorded: it means the kind default
applies, which is policy (ADR 0007). `archive.transition/publication_withdrawal`
version 1 pins the approval it withdraws and its authorizer. The publisher's
own record of an actual send is out of scope here.

**A session is an Entity with ordered parts.** A rehearsal session is an
Entity of kind `recording_session`, whose `label` is its name. Its identity
name is `session-<sha256 of its first part's content>` in namespace
`archive.audio/session`, keeping V1's derivation without its record ID.
Renaming is a revision of that Entity. `archive.claim/session_parts`
version 1 pins the session Entity revision and one or more `part` Artifact
revisions in recording order.

**Door needs new rules.** The candidate bundles are in
[0048-contracts](../proposals/0048-contracts/). They use these profile
keywords, which Door does not yet evaluate:

- `kinds` on a reference target: a set of Entity kinds;
- `x-archive-track-sample-spans`: ordered spans checked per track, with
  `piece` references in track-then-span order;
- `x-archive-distinct-track-content`: no two tracks pin the same content
  digest;
- `x-archive-monotonic-anchors`: anchors strictly increase in both fields;
- `x-archive-distinct-refs`: the reference and aligned Artifacts differ;
- `x-archive-ordered-distinct-refs`: credit and part order is meaningful, and
  duplicates are refused;
- the JSON Schema keyword `maxLength`.

**Migration uses the same chain.** The operator already made V2
performances as renditions. A reviewed V2 performance therefore becomes a
`song_occurrence`, linked to its Work by `performance_work` version 2, rather
than the direct selection → song assertion that ADR 0040 proposed. World then
has one chain to query. ADR 0047's rule 2 reads "one occurrence object" where
it says "one assertion object", and its identity, snapshot, and successor
rules are otherwise unchanged. `archive.migration/audio_song_assertion` is
dropped from the route. `archive.migration/audio_song_match` asserts a
Work for an occurrence revision instead of for a selection.

**An assertion and its review are separate records.** An assertion (a claim)
says something is so: "this excerpt is Paper Lanterns." A review (an assessment)
judges one exact revision of a claim: "I checked that, and it holds." ADR 0005
keeps them apart, so that a disagreement or a later reversal never rewrites
what was said. A reviewed V2 performance carries both: the occurrence and its
Work link are the claims, and its `reviewed` state becomes an `affirmed`
`archive.assessment/judgment` of each claim revision, with an unknown time
where V2 recorded none. This reconciles ADR 0005's rule that legacy review
states become assessments with ADR 0047's rule that only `reviewed` rows
migrate. A later accepted rule may use `withdrawn` for ADR 0047's retracted
histories.

**A session is a local entity kind.** `recording_session` has no Library
Reference Model equivalent; P-14's mapping does not cover it, and this record
does not extend it.

## Operator answers (2026-10-14)

Migration produces occurrences. Simultaneous sources belong to one selection,
and nothing is duplicated per microphone. A local `recording_session` kind is
acceptable. The operator approved the candidate bundles, the drift model, and the
vocabularies on 2026-10-14. The operator also approved installing the bundles
without the context mappings that Door refuses as redefinitions of core terms,
and with judgment's `assessment_of` mapped to `arch:assessment_of`. Before
release, with operator approval, `archive.claim/song_occurrence` version 2 and
`archive.claim/performance_work` version 3 were added so that an occurrence
can group version 2 selections and be linked to a Work.

## What this does not decide

How dense anchors must be for a given recorder pair is a measurement
question for the aligning tool, not a contract rule. Per-kind current rules: World still shows competing titles, kinds, credits,
and judgments without picking a winner. Also the review app's HTTP adapter and
disclosure review (ADR 0041), the publisher, and the specification version
number. The release needs JSON Schemas and context fragments in the existing
contract form, Door validation tests for each rule, World examples, and a
migration rule for existing archives, which remains open.

## Alternatives considered

- **Keep `song` separate from `musical_work`.** Each song would need two
  identities kept in step by a correspondence claim.
- **A superseding withdrawn claim.** This stores a judgment inside the claim,
  which ADR 0005 rejects.
- **One generic label claim.** An open property name weakens Door validation
  and World typing.
- **One selection per microphone, grouped by an occurrence.** Every fact
  would be stated once per source, and a span covering every source of a
  performance would have no single name.
- **Defer publication and sessions.** Smaller, but the app could not replace
  V1 review on its own.
