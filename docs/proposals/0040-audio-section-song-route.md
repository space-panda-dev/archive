# Proposed V2 audio-section-to-song claim contracts

Status: proposed. This document specifies a candidate migration route; it is
not an installed contract, a specification release, or authorization to write
Archive state.

This route supports the first semantic shadow required by
[ADR 0036](../decisions/0036-v2-frozen-migration-bundle.md): a reader can start
from a reviewed V2 audio section, identify the exact selected audio, read the
claim that the selection is a particular song, and follow every source
reference to the frozen migration bundle. It translates source assertions into
Archive meaning without adopting V2's current selector.

The vocabulary follows Archive's four shapes and its terms for claims,
references, and review. The predecessor's accepted audio decision is evidence
about the source data and workflow; it does not decide Archive's identity or
currentness rules (ADR 0015, ADR 0035, ADR 0036).

## Candidate contract set

The specification release that accepts this proposal MUST ship these canonical
contract bundles and their local JSON-LD context fragments, as required by
ADRs 0025 and 0027. The names below are proposed identifiers; the final
version and digest are assigned by the accepted release.

| Contract | Payload or claim meaning |
|---|---|
| `archive.audio/song_entity` | Constrains the core entity payload to the Archive-local `song` kind and gives it a stable identity allocation. |
| `archive.migration/audio_selection` | An ordered selection of exact spans from pinned artifact revisions. |
| `archive.migration/audio_song_assertion` | An assertion that one admitted selection is identified as a distinct song entity, including the source assertion and its review state. |
| `archive.migration/audio_song_match` | A machine assertion linking a selection to an existing song entity, with the actual scored evidence and method. |

The selection and the assertion are separate claims. A section without a
supported song assertion can still have a selection claim if the source rows
support that scoped meaning. A machine match claim asserts the link only when
its versioned matching rule emits an assertion; candidates that do not meet
that rule remain outside Archive claims unless a person reviews them.

The proposed `archive.audio/song_entity` contract constrains an entity payload
to exactly `{"kind":"song"}` or
`{"kind":"song","label":"<non-empty title>"}`. It defines `song`
locally as a distinct musical work identified by the operator; it does not
equate the kind with a MusicBrainz `Work` or `Recording` or claim a Library
Reference Model mapping. The contract owns the `archive.audio/song` identity
namespace. The Archive-to-Library Reference Model mapping remains open
(ADR 0012, ADR 0027).

## Selection payload and references

`archive.migration/audio_selection` has a closed payload with:

```json
{
  "parts": [
    {
      "ordinal": 0,
      "start_sample": 123,
      "sample_rate_hz": 48000,
      "end_sample": 456,
      "source_locator": {
        "member_path": "source/audio-segments/seg_example.json",
        "byte_offset": 0,
        "byte_length": 321,
        "digest": "sha256:<64 lowercase hex digits>"
      }
    }
  ]
}
```

The concrete schema MUST constrain `parts` to a non-empty array; ordinals MUST
be contiguous from zero; each end MUST be greater than its start; sample rate
MUST be a positive integer; and every part MUST name exactly one pinned
`artifact` revision in an envelope reference with role `part`. The reference
array order and `ordinal` order MUST agree. The interval is half-open
`[start_sample, end_sample)` in the named artifact revision, following S-15.
Offsets are never converted through floating point seconds or inferred from a
display timestamp. Conversion from V2 seconds is permitted only when the
source's sample rate and exact integer frame boundaries establish one
unambiguous sample offset; otherwise that part and dependent route are
unresolved. The source decimal, sample rate, and conversion rule remain in the
claim payload or source evidence according to the final schema.

Each part locator addresses exact record bytes in the admitted bundle, not a
path outside it. The selection claim also has one exact pinned
`migration_source` reference to the bundle artifact revision. The `parts`
references pin the exact Archive artifact revisions, not merely their content
digests. Equal bytes in two artifact objects therefore do not collapse their
source occurrence or provenance (ADR 0003, INV-6).

The contract-owned reference set is exact: one `migration_source` reference
with a revision pin, followed by one `part` reference for each payload part,
each with a revision pin and in the same order as `parts`. No other reference
role is allowed. A `source_locator` is a closed object with exactly
`member_path`, `byte_offset`, `byte_length`, and `digest`; `member_path` is a
bundle member below `source/`, offsets are non-negative integers, length is a
positive integer, and digest uses ADR 0021's canonical `sha256:` spelling.
Every locator MUST resolve inside the pinned bundle member and match the exact
byte length and digest before a claim is proposed.

The selection identity uses namespace `archive.migration/audio_selection` and
identity name `v2-segment-<source-id-hex>`, where `<source-id-hex>` is the
lowercase hexadecimal encoding of the UTF-8 bytes of the source segment ID.
The mapping MUST establish from the source schema and accepted V2 audio
decision that this ID names the enduring reviewed selection, with
revision changes represented as revisions of the same object only when the
source explicitly links them. If that premise is false or the ID is absent,
the selection remains unresolved; the route MUST NOT substitute a path, byte
digest, title, or bounds for identity (ADR 0021, ADR 0026, INV-24). Hex encoding
keeps arbitrary source identifiers within ADR 0026's ASCII identity-name
grammar and is injective over their UTF-8 bytes.

## Song assertion payload and references

`archive.migration/audio_song_assertion` has a closed payload that records:

```json
{
  "source_locator": {
    "member_path": "source/song-performances/perf_example.json",
    "byte_offset": 0,
    "byte_length": 234,
    "digest": "sha256:<64 lowercase hex digits>"
  },
  "source_state": "reviewed",
  "source_title": "Example title",
  "assertion_kind": "operator_identification"
}
```

`source_state` is a translated fact about the source row. The schema MUST
enumerate accepted V2 states and their disposition. Only a row whose source
semantics establish an affirmative reviewed identification can become this
assertion. Suggested, rejected, retracted, malformed, and superseded rows MUST
remain represented in the source accounting, but MUST NOT become affirmative
assertions. A successor relationship by itself does not prove that its
predecessor is the current Archive claim; Archive stores the supported
assertion and leaves currentness to an accepted query rule.

The assertion has exactly one pinned reference with role `selection` to the
selection claim and one pinned `song` reference to a distinct song entity.
It also has exactly one pinned `migration_source` reference to the bundle
artifact. Its locator identifies the exact source record that made the
assertion. The contract MUST require that the selection and song references
are different object identifiers and have the expected shapes.

The contract-owned reference set is exactly those three roles, each appearing
once and carrying a revision pin. No `supersedes` relation is inferred from a
source label. A source revision chain is represented by Archive's standard
`supersedes` relation only when the source explicitly establishes that chain
and all predecessor revisions are themselves accounted for and admitted.

The assertion object identity is allocated under
`archive.migration/audio_song_assertion`. A source performance identifier MAY
be the identity name only if accepted source semantics establish it as the
enduring assertion identity. A mutable title, order in a list, source path,
score, or digest cannot supply that identity. Multiple source revisions of one
assertion share one object only if the source's explicit revision relation
supports it; each revision pins the exact source predecessor and preserves
the review state it translates. Ambiguous identity or revision chains remain
unresolved.

The `source_title` preserves the text that accompanied the source assertion;
it does not name the song entity and does not assert that title spelling is
canonical. A title alone, including a title shared by several distinct songs,
cannot mint an entity or link a selection to one.

## First identity and review-app boundary

The V2 review app owns the human workflow for first identification. It may
present a title or candidate and collect the operator's identification, but
the identity it proposes MUST be independent of the title, match score,
selected audio bounds, or filesystem location. On creation, the review app
generates a lowercase RFC 9562 UUIDv4 `song_key`, prefixes it with `song-` to
form the identity name, and reuses that key for retries of the same pending
human review. It does not recycle a key after rejection. The key is carried in
the review proposal, not the admitted entity payload. Door derives the
portable object ID under namespace `archive.audio/song` using ADR 0026.
Reusing an existing song entity requires an explicit reviewed link to that
entity, not a string match.

The review app emits a proposal containing the entity allocation, entity
payload, and any assertion proposal. It does not write Archive records. Door
validates that the entity kind and identity namespace are installed, validates
all cited contracts and exact pins, assigns `at`, and admits the entity and
claims as one atomic proposal when their dependencies require it (ADR 0002,
ADR 0028). A failed entity admission leaves no accepted assertion referencing
it. A title or score may be retained as claim evidence but never repaired into
an identity by Door.

The first entity identity is therefore human-proposed through the review app
and Archive-admitted by Door. A machine score can later support a machine
assertion linking another selection to this already admitted identity. It
cannot create or rename that identity.

## Machine match evidence

`archive.migration/audio_song_match` represents one machine-produced claim
asserting that the compared selection is the same distinct song as the
identified corpus selection. Its closed payload has exactly these fields:

```json
{
  "distance": 0.123,
  "distance_scale": "<matcher-defined scale identifier>",
  "distance_direction": "lower_is_more_similar",
  "method": "<portable algorithm identifier>",
  "method_version": "<version>",
  "rule": "<matching rule identifier>",
  "rule_version": "<version>",
  "maximum_distance": 0.2,
  "title_policy": "equal_after_normalization",
  "title_evidence": {
    "candidate_title": "Example title",
    "corpus_title": "Example title",
    "normalizer": "<identifier and version>",
    "comparison": "equal"
  },
  "run": {
    "id": "<portable run identifier>"
  },
  "evidence_origin": {"kind": "recomputed"}
}
```

The complete payload field set is `distance`, `distance_scale`,
`distance_direction`, `method`, `method_version`, `rule`, `title_evidence`,
`rule_version`, `maximum_distance`, `title_policy`, `run`, and
`evidence_origin`. `title_evidence` is required when `title_policy` is
`equal_after_normalization`; it is absent when `title_policy` is `ignored`.
When present, it is a closed object with
`candidate_title`, `corpus_title`, `normalizer`, and `comparison`. When
present, it also requires exact source locators for both titles in the bundle
or pinned claims that carry those exact locators. The normalizer and comparison
are evidence about this run, not a claim that title spelling is canonical.
`run` is closed and carries a stable run
identifier. `evidence_origin` is either exactly `{"kind":"recomputed"}` or
exactly `{"kind":"bundle_record","source_locator":...}`. The canonical
contract MUST use a finite JSON number whose exact serialized value is
retained. It MUST record any normalization needed to interpret the distance,
the compared and matched selections through pinned references, every input
pin used by the scoring run, the algorithm and version, and the rule that
proposed the link. Where the match relies on a saved V2 report, the report
must have been captured and verified independently; `evidence_origin` then
identifies that report's exact bundle locator. A recomputed score records its
actual run and inputs and MUST NOT claim to originate in a V2 record locator.
If the saved report was disposable and not captured, only a reproducible new
run may support the machine claim.

The contract-owned reference set is exactly one pinned `compared_selection`,
one pinned `corpus_selection`, one pinned `corpus_song_assertion`, one pinned
`song`, and one pinned `run_input` for every run input in payload order. A
bundle-backed report adds exactly one pinned `migration_source` reference.
`run_input` references include exact revision pins; duplicate object/revision
pairs are refused. The pinned `corpus_song_assertion` must assert the same
song entity as the match's `song` reference and must refer to the exact
`corpus_selection` revision. This binds both sides of the score to admitted
evidence instead of treating a title as the match target.

The match claim has pinned references to the compared selection, the corpus
selection, and the proposed song entity, plus the pinned migration bundle
reference when it cites captured source evidence. The contract MUST require
the entity link to be distinct from the selection and MUST preserve all
candidate evidence when several entities are close. A score is evidence in
the producer's own scale, not a probability, a universal confidence value, an
identity, or an assessment. It cannot resolve contradictory claims or
establish currentness (ADR 0005, INV-7, INV-8, INV-24).

The match claim identity is run-scoped under
`archive.migration/audio_song_match`: its name is
`run-<run-id-hex>-<input-set-sha256>`, where the run ID is UTF-8 hex and the
digest is SHA-256 over RFC 8785 canonical JSON for the ordered list of
`{role, object_id, revision_id}` inputs, method identifier/version, and rule
identifier. The score is excluded from identity. This makes changed inputs,
method, or rule distinct claims while a retry of the same run stays quiet
(INV-5). The contract MUST ensure the name remains within ADR 0026's ASCII
token grammar and refuses a source run ID that is missing or unstable.

`distance_direction` is fixed to `lower_is_more_similar` for this matcher,
`maximum_distance` uses the declared `distance_scale`, and the asserted link
requires `distance <= maximum_distance`. `title_policy` is either
`equal_after_normalization`, which requires exact equality of the two title
strings after the named normalizer, or `ignored`, which makes no title claim.
The matcher emits an Archive claim only when these recorded conditions pass.
No project-wide score threshold is implied; each claim carries the applied
threshold and rule version. A raw candidate row below or outside the rule
remains operational evidence and does not become an Archive claim by inclusion
in a report. The corpus selection MUST already have an admitted, human-reviewed
`audio_song_assertion` to the same song entity. This establishes the first
identity through review and lets a later machine assertion relate another
selection to that established entity without creating an entity itself.

## Provenance mapping

Each migrated claim MUST carry Archive envelope provenance as required by
ADR 0022. `by` identifies the versioned migration program with a canonical
Package URL. For a translated operator assertion, `agent` identifies the
original asserting person only if an accepted agent mapping resolves it to a
portable Archive entity or agent identifier. Missing or ambiguous mapping
blocks the claim; the migration process must not impersonate the operator.
For a machine-produced match, `agent` identifies the model/software/process
that actually produced the score. `at` is the time Door admits the record.
V2 writer, agent, event-time, and review values remain exact in the bundle and
are copied into payload only under these closed contract fields; none replaces
Archive `by`, `agent`, or `at`.

Every claim cites its exact installed contract bundle as required by S-12 and
ADR 0027. Every source-backed claim has a pinned `migration_source` reference
and a closed source locator. Envelope `source`, if supplied, preserves one
source schema and source identifier exactly; it is not a substitute for the
bundle pin or locator, and no identifier is fabricated from a path or digest
(ADR 0022, ADR 0035, ADR 0036).

## Query and completeness

The minimum route query starts from an admitted selection claim, joins an
admitted affirmative song assertion by the exact pinned `selection` reference,
then follows the pinned `song` reference to the distinct entity. A result must
be able to retrieve and verify the source locator bytes and the exact artifact
revision spans. It reports conflicts and unresolved references explicitly.
An index or query view is regenerable; it cannot decide currentness or hide a
missing contract, missing bundle member, digest mismatch, stale reference, or
conflicting assertion (INV-4, INV-24).

This route does not define a general current rule. Suggested, rejected,
retracted, and superseded V2 rows remain byte-preserved and explicitly
accounted for. The query may expose them as source-state evidence, but only
the affirmative route contract can produce an assertion. Multiple admitted
claims may coexist; a future query contract must report their conflict unless
an accepted assessment/current rule says otherwise (ADR 0005, INV-7).

Migration MUST report this route unresolved until the three bundles and song
entity kind are installed by a specification release, required archive
artifact revisions and source records are accounted for, Door admits the
claims, and a read-after-admission check verifies their pins and source
locators. A raw bundle lookup or a V2 current selector does not satisfy the
query.

## Decisions deliberately left open

This draft does not accept a song entity kind or a MusicBrainz-to-LRM mapping;
define V2 source identifiers as enduring identities; settle ambiguous
selection or performance revision chains; determine the sample-accurate
conversion for any V2 boundary that is not already represented as integer
samples; choose per-kind currentness; define general query syntax; or authorize
production admission. Each missing mapping keeps the affected route
unsupported (ADR 0015, ADR 0035, INV-24).

Acceptance requires review of the exact canonical schema and context bundles,
their specification version, the identity allocation encoding, the complete
V2 state mapping, and synthetic source-record fixtures covering reversed,
missing, contradictory, stale, and untranslatable references. The bundles
must then be installed and listed in that specification release before
migration code can emit proposals.
