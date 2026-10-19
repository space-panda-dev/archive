# 0082. Named views: the first current rule, correction overlay, differences, and chord names over the relay

Status: accepted (operator, 2026-10-18)

Specification 0.0.9 can hold every chord a machine, a document, or a person
asserts about a recording ([ADR 0077](0077-chord-stacks-and-meter.md)), but
it cannot say which one to show. Asking for the current chord is
unsupported, and the first current rule is an
[open question](../open-questions.md): each candidate rule ranks sources in
a way INV-8 forbids as a universal rule, so a rule must be per question and
must name why it chose. Three things around it are also missing:

- ADR 0077 describes a correction overlay (a correcting claim shown first,
  the corrected one beside it) and a comparison of two players' chords, and
  World computes neither. It projects `arch:corrects` and stops.
- Names are views of a stack, computed in `world/harmony.py`, and nothing
  serves them. A client that shows a chord symbol must either reimplement
  the naming rules, and drift from them as they did once already (ADR 0077,
  amendment 4), or show pitch classes.
- The relay answers SPARQL ([ADR 0060](0060-relay-interface-and-role.md)).
  A rule with tiers, corrections, judgments, and held ends is not a query a
  client should write, and if every client wrote its own, each would be its
  own current rule.

The product these serve is an application for learning songs as lead sheets,
showing and correcting machine output, writing lead sheets for the
operator's own songs, and sharing them with bandmates. Two people may
disagree about a co-written Work, and each asserts their own claim. The
mix's harmony differing from one part's local chord (keys playing a 6/9
chord while a guitar plays a major seventh) is not a disagreement but a
place worth zooming into.

The operator decided the following on 2026-10-18; this record states it as
given.

- **D4.1, canonical on a co-written Work: both.** By default the viewer
  picks whose version to read: the current rule takes a person. Optionally,
  a band-adopted version: a lead sheet asserted with the band as agent
  ([ADR 0078](0078-contributions.md) membership). Personal versions stay
  beside it.
- **D4.2, the first named current rule for chords, without a lead sheet:**
  the chosen person's corrections and affirmations, then aligned documents
  (a Guitar Pro transcription, a chart), then machines in a stated order,
  with the stem bass as the slash bass. This formalises the rule the review
  client's viewer already applies.
- **D3.2, differences are computed.** Machine-detected differences, the mix
  against a part and one tool against another, are views served by World,
  not stored. (Person-asserted flags on a span are annotation claims,
  ADR 0081.)
- **Implied.** There is no weighting across claims (ADR 0005, INV-8): "prefer
  A over B" is said with judgments, affirming A's span and rejecting B's. A
  mix claim (no `part`) and a part claim (`part` names one player) are
  separate claims, compared by a view labelled "difference", never
  "conflict".

## Decision

**A named view is a question World answers by a stated rule.** It has an
identifier, `archive.view/<name>`, and a version, a decimal integer as text,
and states:

- its **question**, in one sentence;
- its **subject**: what the question is about, such as one recording's
  audio (an Artifact revision) or one Work;
- its **parameters**, each with a default that the answer repeats, so an
  answer always says exactly what was asked;
- its **answer**: a JSON object whose shape the version fixes.

World computes a view from the claims and assessments in its selected
generation, when asked. A view's answer is regenerable state: it is never
admitted, never written into an archive, and never stored as evidence
(S-18, INV-7). The relay may cache it as it caches query answers, keyed by
generation and request ([ADR 0063](0063-relay-read-speed.md)). A **current
rule** is a view that chooses. Every current rule is named, answers one
question, names the claim it chose and why in each stretch it answers, lists
every other claim it saw there, and reports a conflict rather than choosing
where its rule cannot (INV-8, INV-24). Nothing makes a claim current except
a rule a viewer asked for by name and version.

**Versions are additive.** A view's version fixes its answer's shape and its
rule. A new optional answer field keeps the version. Any change to what the
rule chooses, to a field's meaning, or to a default is a new version, and the
previous version is served beside it for the overlap window of
[ADR 0059](0059-world-read-contract.md) (at least 90 days after the release
that introduces the new one). A request names its version; a version that is
not served is refused, never answered by another. Every view below is
version 1.

**A view reads only what the caller may read.** The relay gives World only
the records its caller may read
(ADR 0088), and every answer states
`withheld`: how many records were kept from the caller. An empty answer
with nothing withheld means nothing was there.

**Five views.**

| View | Question | Subject |
|---|---|---|
| `archive.view/chord_names` | What are the computed names of these chord readings? | `readings`: 1 to 1,000 readings |
| `archive.view/chord_corrections` | Over this recording, which chord claims correct which, and where? | `source` |
| `archive.view/chord_differences` | Where do a part and the mix, two parts, or two tools hear different chords? | `source` |
| `archive.view/chords_current` | Which chord sounds over these samples, for this person? | `source` |
| `archive.view/lead_sheet_current` | Which lead sheet of this Work does this viewer read? | `work`, optional `recording` |

A `source` subject pins the audio whose samples the chord claims count, as
those claims pin it: `{"id": <object>, "revision_id": <revision>}`. The
chord views read every head `archive.claim/chord_stacks` Claim, version 1
or a later version that keeps its spans and readings
([ADR 0079](0079-machine-harmony-claims.md)), whose `source` reference pins
that revision. Each also takes optional `start_sample` and `end_sample` (an
integer of at least 0, and a later one) to answer for a window. In an
answer, a reading is named by `{"claim": <revision>, "span": <index>,
"reading": <index>}`.

**Judgments, as every view here reads them.** A judgment
(`archive.assessment/judgment`, [ADR 0048](0048-review-app-write-contracts.md))
of version 1 judges a whole claim; one of version 2 may judge a span of
samples or one reading of one span ([ADR 0081](0081-judgments-and-annotations.md)).
Every view reads them through ADR 0081's **standing**, one judge at a time,
and defines no rule of its own for combining them: at each sample, the
narrowest applicable judgment of that judge stands, and equally narrow ones
that disagree give no verdict. A view cuts its stretches where a judged span
starts or ends. What the claim's own agent's standing says is `withdrawn`
is gone from every view; a `withdrawn` verdict from anyone else changes
nothing, since withdrawing is an asserter taking back their own claim
(ADR 0081 refuses it in version 2). `affirmed` and `rejected` count only
where a view takes a person, and only that person's standing.

**`archive.view/chord_names` version 1: names served, so clients never
reimplement them.** For each reading it answers its `form` (`stack`,
`no_chord`, `text`, or `bass_only`, the bass-only form of
ADR 0079) and, for a stack, one
`resolution` per way its uncertain tones can resolve, each with the tones
above the bass and every computed name, in World's stated order (ADR 0077):
`root`, `asserted_root`, `harte`, `symbol` (null when no lead-sheet quality
matches exactly), and `distance`. The answer states `spelling`: `default`
until a key claim's spelling rule is installed
([ADR 0080](0080-key-and-tuning.md)). Readings are checked by ADR
0077's rules first; a malformed one refuses the request. A client may send
readings that no claim holds yet, such as a chord a person is typing into a
lead sheet, so writing and reading use one set of names. Its one parameter,
`transpose` (an integer from -11 to 11, default 0), moves each reading by
that many semitones before naming it, by
[ADR 0089](0089-practice-audio-and-exports.md)'s rule.

```json
{"view": "archive.view/chord_names", "version": "1",
 "subject": {"readings": [{"bass": 7, "tones": [0, 3, 10]}]}}
```

answers, among the names of that one resolution,
`{"root": 0, "asserted_root": false, "harte": "C:min7/5", "symbol":
"Cm7/G", "distance": 0}` and `{"root": 3, ..., "symbol": "Eb6/G"}`.

**`archive.view/chord_corrections` version 1: ADR 0077's overlay,
computed.** Over the samples where a claim's span overlaps a span of a
claim it pins in `corrects`, the correcting span is `shown` and the
corrected one is listed in `corrected` with the claims it was corrected
`by`. Elsewhere every claim stands as asserted, so only corrected
stretches are listed. Where two correcting claims shown over the same
samples disagree, or claims correct each other so that none is left to
show, `conflict` is true, `why` says which, and the view chooses neither.
The overlay follows the citation, never the kind of asserter: a machine's
claim that cites a person's is shown over it. A correction of a chord
sequence is of the whole sequence, which has no samples; this view is about
timed claims only.

```json
{"start_sample": 32000, "end_sample": 48000,
 "shown": [{"claim": "sha256:…", "span": 0}],
 "corrected": [{"claim": "sha256:…", "span": 1, "by": ["sha256:…"]}],
 "conflict": false}
```

**`archive.view/chord_differences` version 1: a difference, never a
conflict.** It pairs chord claims about one recording and compares their
first readings (each asserter's preferred) over every stretch where their
spans overlap. A pair is compared by its `kind`:

- `part_vs_mix`: one claim names a `part` and the other names none. The
  mix's harmony and one player's local chord are both true; where they
  differ is a place to look.
- `part_vs_part`: both name parts, and the parts differ: two players'
  chord tones, the comparison ADR 0077 foresaw.
- `tools`: neither names a part, and both pin runs of different methods
  (ADR 0064): one machine against another.

Two readings differ when the pitch classes that certainly sound (the bass
and the tones) differ, or their basses differ, or exactly one is no chord.
Each difference states `kind`, `label` (always `difference`), its samples,
both sides (`a`, the part where there is one, and `b`, each with its
reading, and its `part` and `method` where known), `only_in_a`, `only_in_b`,
`bass_a`, `bass_b`, and `uncertain`, the classes either side was unsure of.
A text-only or bass-only reading cannot be compared tone by tone; the
answer counts such pairs in `not_compared` rather than dropping them in
silence. A parameter `kinds` asks for some of the three; all by default.

```json
{"kind": "part_vs_mix", "label": "difference",
 "start_sample": 0, "end_sample": 32000,
 "a": {"claim": "sha256:…", "span": 0, "reading": 0, "part": "keys"},
 "b": {"claim": "sha256:…", "span": 0, "reading": 0, "method": "<chord extractor>"},
 "only_in_a": [2, 9], "only_in_b": [11], "bass_a": 0, "bass_b": 0}
```

Here the keys play C D E G A (a 6/9 chord) and the mix is heard as C E G B
(a major seventh). Nothing about a difference is stored, and nothing in it
judges either side. Two people who disagree about the same part assert two
claims and see each other's beside their own in `chords_current`; that is
the disagreement the operator described, and no view relabels it.

**`archive.view/chords_current` version 1: the first current rule.** Its
question is "which chord sounds over these samples of this recording, for
this person?". Its parameters are `person`, the object identifier of the
person whose word comes first (by default the person the caller's login is
bound to), and `machine_order`, 0 to 16 distinct derivation-run method
identifiers, and `transpose` as in `chord_names`, which moves the answer's
`stack` and `names` but never what `chosen` points at. The default order is
the operator's: the two chord extractors
of the feature bank, the learned chord recogniser before the chord-HMM
estimator, whose method identifiers are in `world/views.py`. Over each
stretch of samples where any chord claim speaks, it takes these steps:

1. **What it may choose.** A span may be chosen when it is not withdrawn,
   the person has not rejected it or its claim, and some reading is left
   that the person has not rejected. It is in one of three tiers:
   - `person`: the person asserted the claim (their corrections are their
     claims), or the person affirmed the claim, the span, or one reading
     of it. An affirmed reading is the one chosen;
   - `document`: the claim pins a `document` or a `sequence`: an outside
     source aligned to the recording;
   - `machine`: the claim pins a run whose method is in `machine_order`;
     its rank is its place in the order.

   Any other claim (another person's, or a machine the order does not
   name) is never chosen by this rule, and is listed beside the choice.
   The tiers rank evidence for this question only, as the operator chose
   them for reading lead sheets; they say nothing about belief in general
   (INV-8), and a viewer who wants another ranking asks for another rule.
2. **Corrections among them.** A span the rule may choose that another
   span it may choose corrects (by `corrects`) is not chosen, and the answer
   says by what. A correction by a claim the rule may not choose, such as
   another person's, is not applied: it is listed beside the choice, and
   the other person's view applies it.
3. **The best tier and rank.** Of what is left, the lowest tier, then the
   lowest machine rank. Within it, a span past its first sample whose end
   is `held` (ADR 0077) yields to one whose chord is asserted there: a held
   end says only that the source wrote nothing new.
4. **Agreement.** If what is left agrees (the same stack, or both no chord,
   or the same text), the first by revision is chosen and the rest are named
   as agreeing. If it disagrees, the stretch has a `conflict` listing them,
   and the rule chooses nothing there.
5. **The slash bass.** Where a `machine` reading is chosen, has a bass, and
   has no uncertain tones, and the machines' bass-only claims read from the
   bass stem (claims pinning a run whose payload cites, by bank key, a stem
   whose `output` is `bass`, ADR 0079) that cover
   the stretch agree on a bass the person has not rejected, the answer's
   stack takes that bass: the machine's sounding classes become the tones
   above it, its root is kept, and `bass_from` names the bass reading. Where
   they disagree, the machine's own bass is kept and `why` says so. A
   person's or a document's bass is never replaced.

Each stretch of the answer gives its samples and either `chosen` (the
reading, its `tier`, and its `rank`), the `stack` shown, its `names` (as
`chord_names` gives them), `held`, and `bass_from` where it applies; or
`conflict`; or neither, when no claim there is one the rule may choose. All
three give `why`, one sentence per step that decided, and `others`, every
other reading over the stretch. Stretches with the same outcome are joined.
Samples no claim covers are absent: a gap asserts nothing. The answer lists
in `unresolved_runs` every claim whose run it could not read, since its
method, and so its rank, is unknown.

```json
{"start_sample": 16000, "end_sample": 32000,
 "chosen": {"claim": "sha256:…", "span": 3, "reading": 0, "tier": "machine", "rank": 0},
 "stack": {"bass": 4, "tones": [0, 7], "root": 0},
 "bass_from": {"claim": "sha256:…", "span": 7, "reading": 0},
 "names": [{"tones": [0, 7], "names": [{"root": 0, "asserted_root": true,
            "harte": "C:maj/3", "symbol": "C/E", "distance": 0}]}],
 "held": false,
 "why": ["machine <chord extractor>, 1 in the stated order",
         "the bass is the machines' bass-stem reading"],
 "others": [{"claim": "sha256:…", "span": 3, "reading": 0}]}
```

This rule answers chords on a recording only. A Work's chords without a
lead sheet (an untimed sequence), meter, key, form, and melody have no
current rule; asking stays unsupported, and each needs its own named rule.

**`archive.view/lead_sheet_current` version 1: whose lead sheet.** Its
subject is a `work` (an object identifier) and, optionally, a `recording`;
it reads head lead sheet claims ([ADR 0084](0084-lead-sheets.md))
about that Work, and for that recording when one is named, or for none when
none is. Its one parameter `by` is exactly one of `{"person": <object>}`
(by default the caller's person) or `{"band": <object>}`, an organisation
Entity:

- the chosen agent's one lead sheet for the subject is `chosen`;
- two or more are a `conflict`, and the view chooses none: they are two
  arrangements, and the person says which by withdrawing one;
- every other lead sheet for the subject, by anyone, is in `others`, so a
  personal version stays beside the band's and each person's beside the
  others';
- for a band, `members` lists the band's head `band_membership` claims
  (member, dates) that are not withdrawn, so a reader sees who the band
  was.

A **band-adopted lead sheet** is a lead sheet claim whose envelope `agent`
is the band's organisation Entity. Adopting is asserting a new claim, never
a mark on someone's personal lead sheet, so each person's version and the
band's coexist (INV-7). The specification's envelope names a person, model,
or process as `agent`; letting an organisation be one is a change the
specification and Door need (below). Who may send a record under a band's
name is [ADR 0088](0088-bandmates.md)'s: a member whose own band membership
is among the lead sheet's `adopted_by`. The view itself checks no authority: a claim's agent is
evidence of who asserted it, and governance stays with Door (ADR 0005).

**The relay serves views.** Two routes join `/world/query`, under the same
rules: tailnet identity bound to a person, named origins, JSON bodies, the
answer envelope, and the archive block (ADRs 0056 and 0060).

- `GET /world/views` answers `{"views": [{"view", "version", "question"}]}`,
  every view and version served, from World's registry.
- `POST /world/view` takes `{"view", "version", "subject", "parameters"?}`
  and answers `result`: `{"snapshot", "projection", "view", "version",
  "subject", "parameters", "answer", "withheld"}`, where `parameters` has
  every default filled in, so the answer says exactly which question it
  answers. The caller's bound person is the default `person`; another
  person may be named, since choosing whose version to read is the
  viewer's choice (D4.1).

Its new refusal codes, each with one status as ADR 0060 requires, are
`view_unknown` (404), `view_not_served` (404), `view_request_invalid`
(400, as `query_invalid` is), and `view_subject_not_found` (404). A subject
World holds with no claims is the complete empty answer, not a refusal. A
view never runs Door,
never writes, and never fetches anything outside World's store. A view's
answer is cached under ADR 0063's key, extended by the canonical request,
so a refresh that selects a new generation makes every cached view stale at
once.

**Exchange and practice stay views.** An export of what a viewer sees
(JAMS, MusicXML) renders a view's answer and the claim revisions it names;
it is regenerable and stays unadmitted until it is sent to someone
([ADR 0089](0089-practice-audio-and-exports.md)). Loops and progress are the
application's state, not the archive's
([ADR 0054](0054-door-service-for-applications.md)).

**The rules live in World until a release installs them.**
`world/views.py` holds the registry of served views, the request rules
(fail closed: an unknown view, version, field, or value is refused), and
every rule above. It uses only the standard library, `world/harmony.py`
(names, the reading forms), and `world/judgments.py` (standing).
The registry is the one list that both the listing route and the dispatch
read, so a view cannot be listed and not answered, or answered and not
listed. Until a specification release names these views, they are World's
proposal and no archive depends on them.

**New nouns.** Named view, current rule, tier, difference, and band-adopted
lead sheet enter the [vocabulary](../vocabulary.md). None is a subsystem: a
view is a regenerable computation of World's, and the others name parts of
its answer or a kind of claim. They own no verb beyond World's *project*,
enforce no new invariant, and hold no state.

## Consequences

- Asking for the current chord of a recording becomes supported, for one
  named rule and one named person, and every answer shows what else was
  said there. The open question of the first current rule for chords is
  answered; meter's is not.
- Two bandmates who disagree about a co-written Work each read their own
  version by default, each sees the other's beside it, and the band can
  adopt one without erasing either.
- A person's affirmation and rejection now change what they see, which is
  how "prefer this machine here" is said without a weight across claims.
- The review client's viewer rule moves into World. The client must drop its
  own rule and its own chord naming, and read both through the relay.
- Differences between the mix and a part are findable from the first claim
  that names a part, with no stored flag to keep in step.
- Each change to a rule is a new view version served beside the old one,
  so `world/views.py` keeps the previous handler for the overlap window.
- No invariant changes. INV-7 keeps every claim, INV-8 is kept by naming
  the rule and its question, and INV-24 by conflicts, `withheld`,
  `not_compared`, and `unresolved_runs`.

## Relation to other decisions

- [ADR 0005](0005-claims-review-and-authority.md): disagreement is kept;
  the rule is evidentiary for one question, and adopting a lead sheet for a
  band is governance, which stays with Door.
- [ADR 0048](0048-review-app-write-contracts.md): judgments are how a
  person's preferences reach the rule.
- [ADRs 0056](0056-tailnet-relay.md), [0059](0059-world-read-contract.md),
  [0060](0060-relay-interface-and-role.md), and
  [0063](0063-relay-read-speed.md): the routes, envelope, versioning,
  overlap window, codes, and cache.
- [ADR 0077](0077-chord-stacks-and-meter.md): the overlay and names it
  described are now served; its "current is not yet defined" is answered
  for chords on a recording.
- [ADR 0078](0078-contributions.md): band membership names who a band was.
- ADRs 0079 to 0089 (specification 0.0.10): bass-only readings and runs
  (0079), spelling by key
  (0080), scoped judgments
  (0081), the lead sheet claim
  (0084), bandmates' reads
  (0088), and exports
  (0089).

## Alternatives considered

- **Store the current chord, or a "canonical" flag on a claim.** S-18
  forbids it, and it would be stale the moment a new claim arrived.
- **One universal current rule.** "People over machines" is wrong in both
  directions (ADR 0005), and the operator and a bandmate want different
  answers about the same seconds. A rule takes a person instead.
- **Weights across claims** to express preference. There is no scale across
  asserters (INV-8); judgments already say "this one, not that one".
- **Let clients compute the rule from SPARQL answers.** Each client would
  then be its own current rule, and the review client's present one would
  never be named.
- **Named SPARQL queries instead of views.** Ranking, overlap of spans,
  held ends, and naming are not expressible in SPARQL without reimplementing
  `world/harmony.py` in it.
- **Apply every correction, whoever made it.** Then a bandmate's correction
  would silently change the operator's view; it is listed beside the choice
  instead, and applied in the bandmate's view.
- **Label a part's differing chord a conflict.** The keys and the guitar can
  both be right; "conflict" would invite someone to resolve what is music.
- **A band's lead sheet as a judgment of a person's.** An affirmation says a
  claim is right, not that the band plays it, and editing the band's version
  would then mean editing someone's personal claim.
