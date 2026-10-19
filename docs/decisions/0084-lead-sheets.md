# 0084. A lead sheet is a claim that pins the parts it chose

Status: proposed

A lead sheet is what a musician learns a song from: the chords in bars, the
form with its repeats and endings, the tune, and the words under it. After
specification 0.0.9 an archive can hold each of those as its own claim, by
its own asserter: chords as stacks on a recording or as a sequence about a
Work ([ADR 0077](0077-chord-stacks-and-meter.md)), meter as layers of bars,
and credits ([ADR 0078](0078-contributions.md)). Specification 0.0.10 adds
form and untimed meter (ADR 0083), key (ADR 0080), timed lyric lines
(ADR 0085), and melody (ADR 0086). Nothing says which of them, together, make
someone's lead sheet. Two machine readings, a person's corrections, and a
chart may all cover the same bars, and a lead sheet has to show one of them
first.

The operator decided the following on 2026-10-18; this record states it as
given.

- **D6.1.** A lead sheet is a claim by a person (or a band) about a Work,
  optionally for one recording. It pins the exact chord, meter, form, lyric,
  and melody claim revisions it chose. An update is a new revision. Renders
  (PDF, MusicXML) are regenerable exports.
- **D4.1.** On a co-written Work, both people's versions count. By default the
  viewer picks whose version (the current rule takes a person, ADR 0082).
  Optionally there is a band-adopted version: a lead sheet asserted with the
  band as agent, by its members ([ADR 0078](0078-contributions.md)'s
  membership). Personal versions stay beside it.
- **D11.2.** Exports (PDF, MusicXML, LilyPond, JAMS) are regenerable views
  keyed by the exact claim revisions rendered, like derived media
  ([ADR 0069](0069-derived-media.md)). When the operator sends one to
  someone, that exact file is admitted as an Artifact.
- **Implied.** Instrument tuning and capo per part ride on document-derived
  parts, as optional fields. Practice state (loops, progress) is application
  state, not archival ([ADR 0054](0054-door-service-for-applications.md)).
  No claim is weighted above another by kind (INV-8); "prefer A over B" is a
  judgment.

## Decision

**`archive.claim/lead_sheet` version 1: one asserter's choice of inputs for
one Work.** A lead sheet asserts no reading of the music. It says: these
revisions, in this order, are my lead sheet of this Work. Every musical fact
it shows stays in the claim that asserts it, with that claim's own asserter
and provenance; the lead sheet copies none of them. Its references:

| Role | Count | Pins |
|---|---|---|
| `work` | exactly 1 | an Entity revision of kind `musical_work`: what the lead sheet is of |
| `source` | 0 or 1 | the Artifact revision of the recording's mix, for a lead sheet of one recording |
| `recording` | 0 or 1, only with `source` | as ADR 0078's recording scope: a `recording` Entity, an `audio_selection` or `song_occurrence` version 2 Claim, or an Artifact |
| `chords` | 0 to 16 | `chord_stacks` or `chord_sequence` Claim revisions, version 1 or 2 (ADR 0079) |
| `meter` | 0 to 4 | `meter_layers` Claim revisions: version 1, or version 2 timed or untimed (ADRs 0079 and 0083) |
| `form` | 0 to 4 | `form` (untimed) or `form_spans` (timed) version 1 Claim revisions (ADR 0083) |
| `key` | 0 to 4 | `key_sequence` (untimed) or `key_regions` (timed) version 1 Claim revisions (ADR 0080) |
| `lyrics` | 0 or 1 | the lyric document's Artifact revision; its words are never copied |
| `lyric_timing` | 0 to 4, only with `lyrics` | `timed_lyrics` version 1 Claim revisions (ADR 0085), each placing exactly that document |
| `melody` | 0 to 4 | `melody` (Work level) or `melody_spans` (recording level) version 1 Claim revisions (ADR 0086) |
| `credits` | 0 to 32 | `contribution` version 1 Claim revisions of scope `work` about the same Work |
| `document` | 0 to 8 | Artifact revisions a part's tuning or capo was read from |
| `derived_from` | 0 to 8 | `lead_sheet` version 1 Claim revisions of the same Work it started from |
| `band` | 0 or 1 | the band's organisation Entity revision, for a band's lead sheet |
| `adopted_by` | 0 to 16 | `band_membership` version 1 Claim revisions of the members who adopted it |

A lead sheet pins at least one `chords` or `melody` reference. Every
reference pins an exact revision, and no revision is pinned twice. There is no
`run`: a machine never asserts a lead sheet. A machine's chords enter a lead
sheet only because a person or a band chose them.

**Its scope is a Work, and optionally one recording.** A lead sheet of the
Work alone pins no `source`, and then pins only untimed inputs: a chord
sequence, untimed meter and form, a Work-level melody, a key over sections.
A lead sheet for one recording pins that recording's mix as `source`, and
every timed input it pins (any pinned Claim that itself pins a `source`)
counts the samples of that same Artifact revision. It may pin untimed inputs
too: the Work's form beside a recording's chords. An input that pins a `work`
is about the lead sheet's Work object, at that or any other revision of it, so
renaming the Work does not orphan its sequences. A lead sheet's scope never
changes across its revisions; the same music on another recording is another
lead sheet, which may pin this one in `derived_from`.

**The order of references within a role is the asserter's precedence.**
Where two pinned chord claims cover the same samples, or the same sequence
entries, the earlier reference is shown and the later one beside it, as the
lead sheet's own statement of which it chose. The same holds for meter, form,
key, lyric timing, and melody. This is a choice inside one claim, attributed
to its asserter, not a ranking across claims: another person's lead sheet may
order the same inputs the other way, and both stand (INV-8). A typical lead
sheet for a recording pins a person's correction first, the machine claim it
corrects second, and the Work's sequence third, so the corrected seconds show
the person's chord, the rest the machine's, and bars no timed claim covers the
sequence's.

**Payload.** A closed object with two optional fields:

- `label`, 1 to 200 characters: the asserter's name for this lead sheet among
  their others of the same Work ("Full chart", "Capo version"). It is never
  identity and never a quoted lyric line.
- `parts`, 1 to 16 objects, each with `name` (1 to 100 characters, distinct;
  an input whose `part` text equals it is shown in that part), and optional
  `instrument`, a MusicBrainz instrument URI as in ADR 0078; `strings` and
  `capo`, the instrument fields of a document-derived part exactly as
  [ADR 0086](0086-notes-and-melody.md) defines them for a notes claim (1 to
  12 open strings as MIDI note numbers in tablature order, lowest staff line
  first; a capo fret 1 to 24, only with `strings`); and `document`, the
  0-based index of the `document` reference the strings were read from,
  given only with `strings`. Strings with no `document` are the asserter's
  own statement, as a songwriter knows how they tune.

```json
{
  "agent": "urn:uuid:…(a person)",
  "payload": {
    "label": "Full chart",
    "parts": [
      {"name": "Guitar", "instrument": "https://musicbrainz.org/instrument/63021302-86cd-4aee-80df-2270d54f4978",
       "strings": [38, 45, 50, 55, 59, 64], "capo": 2, "document": 0},
      {"name": "Voice"}
    ]
  },
  "refs": [
    {"role": "work", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "chords", "id": "urn:uuid:…(a chord sequence about the Work)", "revision_id": "sha256:…"},
    {"role": "meter", "id": "urn:uuid:…(untimed bars, ADR 0083)", "revision_id": "sha256:…"},
    {"role": "form", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "lyrics", "id": "urn:uuid:…(the lyric document)", "revision_id": "sha256:…"},
    {"role": "melody", "id": "urn:uuid:…(the Work's tune, ADR 0086)", "revision_id": "sha256:…"},
    {"role": "credits", "id": "urn:uuid:…(composer, scope work)", "revision_id": "sha256:…"},
    {"role": "document", "id": "urn:uuid:…(a transcription file)", "revision_id": "sha256:…"}
  ],
  "supersedes": []
}
```

**Who asserts it.** The envelope `agent` is a person's or an organisation's
Entity URN (D-3); software, a model, or a process is refused. A person's lead
sheet pins no `band` and no `adopted_by`.

**A band's adopted lead sheet (D4.1).** A band's lead sheet has the band as
its `agent`, pins that band's organisation Entity as `band`, and pins in
`adopted_by` one `band_membership` claim per member who adopted it, each a
membership of that band and no two naming the same member. It usually pins
the personal lead sheet it adopted in `derived_from`. The personal lead sheets
stay, unchanged and valid, beside it; adoption supersedes nothing. Which view
a reader sees first is ADR 0082's current rule, which takes a person or the
band; this record defines none.

**An update is a new revision; another person's version is a new lead
sheet.** A lead sheet's next revision supersedes the previous one (D-3) and
keeps its `agent`, its Work object, and its `source` revision. A bandmate who
disagrees with the songwriter's seventh asserts their own chord claim and
their own lead sheet, which may pin the songwriter's in `derived_from`: two
lead sheets, two asserters, neither superseding the other.

**A lead sheet is not a correction.** A correction
([ADR 0077](0077-chord-stacks-and-meter.md)) is a reading of the music: it
says what sounds over its samples and names the claims it corrects, and
World's resolved view of a recording's chords shows it first wherever it
overlaps them. A lead sheet says nothing about what sounds; it changes no
resolved view, and pinning a machine claim second does not reject it. To fix
a chord in a lead sheet, a person asserts a correction (or a new sequence
revision) and makes a new lead sheet revision that pins it. To say an input
is wrong, a person judges it (`archive.assessment/judgment`, ADR 0048, or a
scoped judgment, ADR 0081).

**Inputs stay pinned when they change.** When a pinned input is later
revised, corrected by another claim, or judged, the lead sheet still pins
the revision it chose, and its renders do not change. World computes, never
stores, what changed since: for each shown input, every later revision of the
same object, every claim that names it in `corrects`, and every judgment of
that exact revision with its verdict. A client offers the asserter a new
lead sheet revision; nothing makes one for them. Lineage (`derived_from`) is
not an input, so a revised source lead sheet is not reported as a change.

**Renders are views (D11.2).** A PDF, MusicXML, LilyPond, or JAMS render of a
lead sheet is regenerable from its revision, which pins every input exactly,
and from the render's own parameters (a transposition, a paper size, the
parts chosen). A render is [ADR 0089](0089-practice-audio-and-exports.md)'s
export of the lead sheet revision: its request renders that revision, joins
the lyric document it pins, and passes the view's parameters as options, and
it is filed by ADR 0089's export key, which is the only key a render has.
Losing a render loses nothing. A render joins the lyric document's words at render time, as
ADR 0077 requires, so a render carries the lyrics. When the operator sends a
render to someone, that exact file is admitted as an Artifact and tied to the
lead sheet revision it renders by the claim ADR 0089 defines for shared
exports; the publication approval pins both (ADR 0010).

**Showing a lead sheet shows its inputs.** The pinned work, source,
recording, chords, meter, form, key, lyrics, lyric timing, melody, and
credits are what a render or a view shows. The relay checks each one's
sensitivity and rights (ADR 0085) before it shows or sends any of them; the
lead sheet's own disclosure never widens an input's. Sharing a co-written
Work's lead sheet beyond the band needs each co-writer's approval (ADRs 0085
and 0088). Lineage, the band and its memberships, and the documents a tuning
was read from are not shown.

**The rules live in World until a release installs them.**
`world/lead_sheets.py` holds every rule above as World will re-check it, the
changes-since view, the list of shown inputs, and the export request a render makes (keyed by ADR 0089). It uses only
the standard library, so a client can check a proposal before Door does.
Door cannot admit the contract until a specification release installs its
bundle; that release adds the Door profile rules this record needs: an
envelope `agent` of a person or organisation Entity, a `band` reference equal
to the agent, references whose target contract set depends on the role, a
pinned Claim's own `source` and `work` references compared with the lead
sheet's, a lyric timing's document compared with `lyrics`, the
`band_membership` band compared with `band`, the same-agent and same-subject
rule across a supersession, and a part's `document` index within the
`document` references.

**New nouns.** Lead sheet, adopted lead sheet, part (of a lead sheet),
instrument tuning, capo, changes since, and render enter the vocabulary.
None is a subsystem: each names a claim, part of its payload, or a view of
claims. They own no verb beyond Annotation's *assert*, enforce no new
invariant, and hold no state outside the claims Door admits.

## Consequences

- A songwriter can author a lead sheet of their own song before any
  recording exists: a sequence over their lyric document, untimed bars, form,
  and a tune, all about the Work. Each later recording gets its own lead
  sheet, which may start from the Work's.
- A learner sees machine chords and a person's corrections on one sheet, in
  the order the sheet's asserter chose, with each chord still attributed to
  whoever asserted it.
- Two bandmates who disagree each keep their own lead sheet; a band can adopt
  one without either being lost. The difference between a mix's harmony and
  one part's local chord stays in the inputs and ADR 0082's differences view;
  a lead sheet never calls it a conflict.
- A printed or exported lead sheet can always be made again, exactly, from
  the revision it was made from; a shared one is kept as the file that was
  sent.
- A lead sheet's inputs can go stale without it changing; the
  changes-since view is how an asserter learns that, and a new revision is
  how they act on it.
- No invariant changes. INV-7 keeps every input and every lead sheet; INV-8
  holds because precedence is a stated choice inside one claim.

## Alternatives considered

- **A lead sheet as one big claim holding its chords, bars, form, and tune.**
  It would copy what other claims already assert, lose each fact's own
  asserter and provenance, and need a correction contract of its own. Pinning
  keeps one home per fact.
- **Pinning objects ("the latest revision") instead of revisions.** A render
  would change under its reader when an input was revised, a shared PDF could
  not be regenerated, and an approval could not pin what was published
  (ADR 0010).
- **Updating a lead sheet automatically when an input is corrected.** It
  would assert a choice its asserter never made. The changes-since view
  reports; the asserter decides.
- **A lead sheet as a kind of correction.** A correction asserts what sounds
  and changes the resolved view; a lead sheet asserts a choice and changes
  nothing. Merging them would make every lead sheet a judgment of every input
  it left out.
- **A machine-asserted lead sheet.** A generator can propose one, but the
  choice of inputs is a person's or a band's; a machine's proposal is a draft
  in a client until someone asserts it.
- **Band adoption as a judgment of a person's lead sheet.** A judgment affirms
  a claim but cannot be the band's own revisable version, and a band's
  version that departs from every member's needs to be its own claim.
- **Renders as Artifacts from the start.** Every transposition and paper size
  would become permanent evidence (as ADR 0069 rejected for peaks); only the
  file actually sent is evidence.
- **Instrument tuning and capo on every chord claim.** They describe how a
  part is played from a sheet, often read from a transcription; a chord stack
  is the sounding pitches, which a capo does not change.
