# 0089. Practice audio from the stems, transposition, and exports that are views until shared

Status: accepted (operator, 2026-10-18); note added 2026-10-19 (sends beyond
the circle, and what the 0.0.10 amendments add to exports); amended
2026-10-20 (ADR 0113) and 2026-10-22 (ADR 0116: the `ireal` format)

A person learning a song from a lead sheet wants to hear it without their
own part: the recording minus the bass, for the bassist; the vocal alone, to
learn a line. They also want the lead sheet on paper or in a notation
program, and to send it to a bandmate. Three things stand in the way:

- The stems already exist. The `stems` extractor separates a recording into
  `vocals`, `drums`, `bass`, and `other` as 16-bit FLAC `file` features in
  the feature bank ([ADR 0075](0075-stems-as-content.md)). But a feature is
  never admitted ([ADR 0073](0073-feature-bank.md)), and the relay serves
  only an Artifact's derived media, peaks and a listening copy, and only to
  the active operator ([ADR 0069](0069-derived-media.md)). Nothing
  makes a mix with some stems left out, transposed, or slowed down.
- [ADR 0077](0077-chord-stacks-and-meter.md) names the exports it expects,
  JAMS and MusicXML `<harmony>`, and builds only JAMS. Nothing says what an
  export of a lead sheet is: evidence, a cache, or something in between.
- When a person sends a file to someone, the exact bytes the recipient holds
  matter later ("the chart I sent you said a minor seventh"), but the claims
  it was rendered from may be corrected, and the exporter may change.

The operator decided the following on 2026-10-18 (0.0.10 decisions D11.1 and
D11.2); this record states it as given.

- **D11.1**: the relay serves a recording's bank stems and derived practice
  mixes (minus bass, and so on) by recording digest and feature name, gated
  by the recording's sensitivity. They stay regenerable and are never
  Artifacts unless chosen.
- **D11.2**: exports (PDF, MusicXML, LilyPond, JAMS) are regenerable views
  keyed by the exact claim revisions rendered, like derived media. When the
  operator sends one to someone, that exact file is admitted as an Artifact.
- **D11.3**: practice audio supports any subset of stems (minus drums,
  minus drums and bass), not one at a time: a mix is keyed by recording,
  stem set (a 4-stem or 6-stem separation), and gains. Renders are
  regenerable derived media, cached on demand.
- **D11.4**: chord display transposes as a view parameter (semitones), with
  spelling from the transposed key ([ADR 0080](0080-key-and-tuning.md)).
  Audio transposes by pitch-shifting, offline on a machine and cached by
  recording, stem set, semitones and speed, or live in the client. Unpitched
  stems (drums) are not shifted. Nothing transposed is ever a claim.
- **D11.5**: browser-first and server-capable. The client mixes stems and
  pitch-shifts or time-stretches live, for an instant response; it may also
  ask for a server render of the same settings (recording, stem set, gains,
  semitones, speed), cached as derived media, and switch to it at the same
  musical position when it is ready. Which path wins is decided by testing;
  both must exist. This record defines the render request and answer and
  the cache key; the relay serves renders like other practice audio.
- **Implied**: practice state (loops, progress) is application state, not
  archival ([ADR 0054](0054-door-service-for-applications.md)).

## Decision

### Practice audio

**Two paths make the same mix (D11.5).** A practice application mixes the
recording's stems itself, with a gain per stem, and pitch-shifts and
time-stretches them live: it fetches each stem it plays from the relay (the
bank route below) and responds at once to every change. The same settings
can also be sent to the relay as a **practice request**, which a machine
renders into one file, a **practice render**, cached as derived media; when
it is ready, the client may switch to it. Neither path is the archive's:
both are regenerable views of the stems, and which one a client prefers is
decided by testing, not here. Both must exist.

**A practice request names everything that decides the mix**
(`check_practice_request` in `world/exports.py`):

```json
{
  "content": "sha256:<recording hex>",
  "stems": {"extractor": "stems", "key": "sha256:<stems entry key>"},
  "gains": {"vocals": 100, "other": 80},
  "semitones": -2,
  "speed": 75,
  "renderer": {"id": "practice-rubberband", "version": "1"}
}
```

- `content` is the recording's content digest; `stems` names the bank entry
  the stems come from, by extractor and key, as
  [ADR 0079](0079-machine-harmony-claims.md) cites a stem. A 4-stem and a
  6-stem separation are two entries (the bank's `stems` and the `stems-6s`
  its 0.0.10 work adds, with `guitar` and `piano`), so the stem set is named
  by its entry's key, never by a count.
- `gains` names 1 to 16 of that entry's `file` outputs, each with an
  integer gain in percent, 1 to 200. **Any subset is a mix (D11.3):** a stem
  left out is not in it, so "minus drums and bass" is the other stems, and
  one stem alone is a solo.
- `semitones`, -12 to 12 (default 0), shifts pitch; `speed`, 25 to 200
  percent (default 100), stretches time without changing pitch.
- `renderer` names the renderer and its version, which stands for its code
  as an extractor's does (ADR 0073).

Every number is an integer, so canonical JSON has one spelling. Stems whose
output is `drums` are never pitch-shifted (D11.4), in a render or live; a
renderer that changes that list is a new renderer version.

**The practice key names the file.** It is the SHA-256 of the canonical
JSON of `content`, `stems`, `gains`, `semitones`, `speed`, and `renderer`,
defaults filled in, so one mix has one key however it was asked for
(`practice_key`). The file is 16-bit FLAC at the stems' rate, filed in the
recording's own directory under the derived-media root, as ADR 0069 files
derived media: `sha256/<ab>/<cd>/<content hex>/practice/<key hex>.flac`,
with the request beside it as `<key hex>.json`. Destroying the recording
deletes its renders with its other derived media; losing a render loses
nothing.

**The same musical position.** A render starts where the recording starts
and is stretched evenly, so the recording's second `t` is the render's
second `t × 100 / speed` (`render_seconds`, and its inverse
`original_seconds`). A client switching from its live mix to the render
seeks there, and every claim's samples still count the recording's own
timeline. Seamless switching (a crossfade, a matched buffer) is the
client's; this record fixes only where to seek.

**The relay renders on request.** `POST /media/{digest}/practice` takes a
practice request whose `content` is `{digest}` and answers, in the relay's
envelope ([ADR 0060](0060-relay-interface-and-role.md)):

```json
{"key": "sha256:<practice key>", "status": "ready", "media_type": "audio/flac",
 "bytes": 41943040, "speed": 75, "semitones": -2}
```

`status` is `ready` (the file exists) or `rendering` (the relay has begun
it, and the client asks again); otherwise the request is refused:
`stems_not_made` when the named stems entry is not in the bank, and
`renderer_unknown` when the relay has no such renderer installed. The relay
renders at most one file at a time, in the background, with its installed
renderer (one built on `ffmpeg` and a time-stretching library; none is
built by this record), and never separates stems itself: separation takes
most of an hour a recording-hour (ADR 0075). `GET
/media/{digest}/practice/{key}` answers a ready render's bytes, with byte
ranges, its media type, an entity tag of its key, and private, immutable
caching. A render is refused or served under the same gate as the stems
(below).

**The relay serves configured audio features by recording digest and
feature name (D11.1).** The stems a client mixes live come from two routes
added beside ADR 0069's:

- `GET /media/{digest}/bank/{extractor}/{output}` answers the bytes of one
  `file` feature of the content `{digest}`, such as
  `/media/sha256:<hex>/bank/stems/bass`, with byte ranges, the
  output's declared media type, an entity tag naming the entry's key and the
  output, and caching that is private and immutable. A media element seeks
  within a long recording by ranges; the relay cuts no excerpts.
- `GET /media/{digest}/bank` answers, in the relay's JSON envelope,
  which of the configured features exist for that content, so a client can
  offer only those:

```json
{
  "ok": true,
  "archive": {"archive_id": "…", "spec": "archive/0.0.10", "role": "scratch", "projections": [6]},
  "result": {
    "content": "sha256:<recording hex>",
    "features": [
      {"name": "stems/bass", "media_type": "audio/flac", "bytes": 31457280,
       "sha256": "sha256:<stem hex>", "key": "sha256:<stems entry key>"},
      {"name": "stems/drums", "media_type": "audio/flac", "bytes": 31457280,
       "sha256": "sha256:<stem hex>", "key": "sha256:<stems entry key>"}
    ],
    "missing": ["stems/vocals"]
  }
}
```

The relay finds an entry only by the key its installed extractors give
(ADR 0073): it reads the bank through `enrich/bank.py`, which needs only the
standard library, and never searches for "any stems entry". It never runs an
extractor: separation takes most of an hour a recording-hour
(ADR 0075). A feature not yet made is refused with `feature_not_made`, and
the operator, or the import chain when it runs `stems`, makes it.

**What is served is configuration; to whom is the recording's answer.**
The relay's configuration names a `bank_root` (which may be the derived-media
root) and `practice_features`, the feature names it serves; an empty list,
the default, serves none. A request is answered only when all of these
hold, in this order:

1. the name is configured, and an installed extractor declares it as a
   `file` output whose media type is `audio/*`; otherwise
   `feature_not_served`. A series, a scalar, or a file that is not audio is
   never served by this route;
2. `{digest}` is the content of an Artifact in World's selected generation,
   as for ADR 0069's routes; otherwise `media_unknown_digest`;
3. the caller is the active operator's person, or the recording may reach
   the caller under its sensitivity, rights, and disclosure as ADRs 0085
   and 0088 decide for the recording itself; otherwise `media_forbidden`.

A stem or a practice render is the recording (ADR 0075). It takes the
recording's answer and never has a sensitivity of its own, so a recording
marked for the operator alone has stems for the operator alone, and a
recording disclosed to the band's circle has stems the band may practise
with. When several Artifacts share the content, the most restrictive answer
among them applies (S-32). A fact that cannot be established (no
sensitivity claim, a World that cannot answer) never widens: only the
active operator is served without one. The same gate replaces ADR 0069's
operator-only rule for `peaks` and `listen`, so a bandmate who may hear a
recording may also see its waveform and play its listening copy.

`world/exports.py` holds this decision as `practice_refusal`, so the relay
calls one tested function rather than deciding in its request handler.

**Serving is not a new disclosure.** A non-operator is served only a
recording that ADR 0088's circle disclosure already lets reach them, and
that disclosure is recorded before the first byte leaves (ADR 0016). The
stems and renders of that recording are the same content in other forms, so
the relay records nothing per request, and logs the caller's agent, never
the login (ADR 0060). A disclosure record may overcount and never
undercount; it is not made again for every range a player fetches.

**Never an Artifact unless a person chooses.** A stem stays in the bank and
a practice render in derived media, regenerable and deletable at any time.
A person who wants one kept as evidence, for example because it was sent to
someone outside the relay, admits its bytes as an Artifact with an operator
tool: the relay admits no content (ADR 0056), and [ADR 0085](0085-documents-lyrics-rights.md)'s document route
refuses audio. ADR 0079 decides only that a chosen stem's digest is then the
one its citations name; what claim records where an admitted stem or render
came from (its mix, separator, and settings) is not decided by either
record, and is [open](../open-questions.md#claims-and-provenance).

**Practice state is application state.** Loops (a start and end sample),
the speed, pitch shift and gains a person last chose, which path is
playing, and how often a passage has been played are a practice application's own state,
kept where it keeps its own (browser storage, or its own store). None goes
through Door or into World; losing it loses nothing archival
([state model](../state-model.md)). A loop that a person wants others to
find ("this bar is hard") is not practice state but an annotation on a span,
a claim under [ADR 0081](0081-judgments-and-annotations.md).

### Transposition is a view (D11.4)

**Chords transpose as a view parameter.** A `transpose` parameter, an
integer number of semitones from -11 to 11 (default 0), moves every pitch
class of what a view shows before it names it: bass, tones, root, uncertain
groups, and a voicing (which is dropped where it would leave MIDI's range).
The source's verbatim `text` is dropped from a moved reading, since it names
the chord before the move. A key reading moves with it: its collection and
tonic move and its mode stays, and its asserter's own `spelling` and `text`
are dropped, so the moved key is spelled by ADR 0080's stated rule and the
chords by the moved key (Gb major up a semitone is G major, with an F#).
`transpose_reading` and `transpose_key` in `world/exports.py` are this rule.
[ADR 0082](0082-views-and-current.md)'s views that answer chord names
(`chord_names`, `chords_current`) take the parameter, which their answers
repeat as they repeat every parameter, and an export takes it as an option
(`transpose`), so a transposed chart is regenerable like any other.
**Nothing transposed is ever a claim**: a person who decides the song is in
another key asserts a key claim (ADR 0080), and a chart written in another
key is its own sequence.

**Audio transposes by pitch-shifting,** live in the client or in a practice
render's `semitones` (above), and never moves an unpitched stem.

### Exports

**An export is a view of exact revisions, rendered by a named exporter.**
Four formats are named:

| Format | Media type | Suffix | What it renders |
|---|---|---|---|
| `pdf` | `application/pdf` | `.pdf` | an engraved lead sheet or part |
| `musicxml` | `application/vnd.recordare.musicxml+xml` | `.musicxml` | MusicXML 4.0 `score-partwise`: bars, `<harmony>`, notes, lyrics |
| `lilypond` | `text/x-lilypond` | `.ly` | the LilyPond source a PDF is engraved from |
| `jams` | `application/json` | `.jams` | timed claims as JAMS annotations (ADR 0077's mapping) |

An **export request** says everything that decides the file:

```json
{
  "format": "pdf",
  "exporter": {"id": "lead-sheet-lilypond", "version": "1"},
  "rendered": [{"id": "urn:uuid:…", "revision_id": "sha256:…"}],
  "joined": [{"id": "urn:uuid:…", "revision_id": "sha256:…"}],
  "options": {"transpose": 2, "staff_size": 20, "chord_font": "jazz"}
}
```

- `exporter`: the exporter's `id` (lowercase letters, digits, and hyphens)
  and `version` (letters, digits, and dots, at most 40). As for an extractor
  (ADR 0073), the version stands for the code: an edit that can change an
  output comes with a new version.
- `rendered`: 1 to 64 distinct exact Claim revisions, in the export's order
  (a JAMS file's annotations, a score's parts). A lead sheet's export renders
  the lead sheet's revision ([ADR 0084](0084-lead-sheets.md)), which already pins the exact chord,
  meter, form, lyric, and melody revisions it chose; an export of a single
  chord stacks claim to JAMS renders that claim.
- `joined`: 0 to 16 distinct exact Artifact revisions whose bytes the
  exporter reads, such as the lyric document whose lines a timed lyric claim
  places (ADR 0085). Lyrics are joined at export and never copied into a
  claim (ADR 0077). A revision is never both rendered and joined.
- `options`: at most 32 named scalars the exporter reads (a transposition,
  a paper size), each a boolean, an integer, or text of 1 to 200
  characters. There are no fractional numbers, so canonical JSON, and the
  key, has one spelling.

**The key names the file.** An export's key is the SHA-256 of the canonical
JSON (sorted keys, no spaces, UTF-8, as the feature bank's keys) of its
format, exporter, options, and the `[id, revision_id]` pairs of `rendered`
and `joined` in order; absent options and joins are the empty ones. The
file lives under the derived-media root at
`exports/<ab>/<cd>/<key hex>/export<suffix>`, with the request beside it as
`request.json`. Like derived media it is regenerable: losing it loses
nothing, and nothing is invalidated in place, since a corrected claim is a
new revision and so a new key. It is filed by key, not by a content digest,
because an export reads many revisions and its own bytes are not known until
it is made.

**Every export says what it dropped.** An exporter writes, beside the file,
the losses of ADR 0077's table that apply (alternative readings, weights,
uncertain tones, a root that does not sound, a voicing in `<harmony>`, a held
end), and JAMS keeps them in each annotation's sandbox as ADR 0077 says.

**MusicXML `<harmony>` is mapped as ADR 0077 sketched.** For one span's
readings, the first reading is written and every other is a loss:

- a stack's `<root>` is its asserted root, or else the root of its first
  computed name in ADR 0077's stated order; the `<kind>` is the MusicXML
  kind whose intervals need the fewest `<degree>` marks (fewer
  subtractions at a tie, then the order of MusicXML's own list), with the
  computed lead-sheet symbol, less root and bass, as its `text`; a bass
  that is not the root is `<bass>`; each added interval is a `<degree>`
  `add`, named as an extension above a seventh and a simple degree below
  one, and each missing kind interval other than the root a `subtract`. So
  a stack G under C E♭ B♭ is `C` `minor-seventh` over `G`; C E G B♭ with D♯
  is `dominant` with an added raised ninth; a lone note is `power` with
  its fifth subtracted;
- a root that does not sound is written as the root and reported lost;
- "no chord" is kind `none` with the text `N.C.` over a nominal C, since
  MusicXML requires a root;
- a text-only reading writes no `<harmony>`, and is reported lost;
- pitch classes are spelled by the key claim in force (ADR 0080) when the
  exporter is given one, and otherwise by World's stated default spelling.

`musicxml_harmony` in `world/exports.py` is this mapping; the functional
kinds (Neapolitan, Italian, French, German, Tristan) and `pedal` are never
chosen from a stack.

**The relay renders on request, for those who may read what is rendered.**
`POST /exports` takes an export request and answers, in the envelope, its
`key`, `media_type`, `bytes`, and `losses`, rendering the file when it is
missing; `GET /exports/{key}` answers the file's bytes. An export is
answered only to a caller who may read every rendered revision and every
joined Artifact: the operator, or a bandmate under ADR 0088's circle rules
and the joined documents' own sensitivity (ADR 0085). Rights never limit the
circle, so a document licensed for personal use only (bought tablature, for
example) may be joined in an export for the circle, and in none sent beyond
it. A request
naming an exporter that is not installed is refused with
`exporter_unknown`. No exporter is built by this record; until one is, the
routes refuse every request with that code.

**A sent export is admitted, exactly.** When a person sends an export to
someone, the exact file is admitted first, as an Artifact, with one
`archive.claim/export` version 1 claim saying what it renders; then the
send is recorded as a disclosure (ADR 0016), and only then do the bytes
leave. The Artifact is what the recipient holds, whatever later revisions or
exporter versions would render. Onward sharing is the operator's
(ADR 0088), so the claim's `agent` is the operator; a co-written Work's lead
sheet sent beyond the band needs every co-writer's consent first
(decision D12.2, [ADR 0088](0088-bandmates.md)'s sharing gate). Beyond
the circle, the send needs a publication approval version 2, which Door
checks against reach and the sharing gate, and the sender asks World's
`may_publish` before the bytes leave
([ADR 0085](0085-documents-lyrics-rights.md), amendment of 2026-10-19). The
exporters write articulation, the bass line's own staff, and syllables
under their notes as [ADR 0086](0086-notes-and-melody.md)'s and ADR 0085's
amendments of 2026-10-19 say. A PDF, MusicXML, LilyPond, or JAMS file is a
document, so ADR 0085's upload route can stage it; the relay itself still
admits no content.

**`archive.claim/export` version 1.** Its references are:

- exactly one `artifact`: the exported file's Artifact revision;
- 1 to 64 `rendered`: exact Claim revisions, in the request's order;
- 0 to 16 `joined`: exact Artifact revisions, in the request's order.

Its payload has `format`; `media_type`, which is the format's (above);
`exporter` (`id`, `version`); optional `options`; `key`, which must equal
the key of the request its references and payload state; optional
`software`, 1 to 32 `{name, version}` pairs the exporter reports (an
engraver's version), as a derivation run records them (ADR 0064); and
optional `losses`, 1 to 32 distinct texts of 1 to 200 characters. The file
is never one of its own inputs.

```json
{
  "payload": {
    "format": "pdf",
    "media_type": "application/pdf",
    "exporter": {"id": "lead-sheet-lilypond", "version": "1"},
    "options": {"transpose": 2, "staff_size": 20, "chord_font": "jazz"},
    "key": "sha256:<export key>",
    "software": [{"name": "lilypond", "version": "2.24.4"}],
    "losses": ["alternative readings", "uncertain tones"]
  },
  "refs": [
    {"role": "artifact", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "rendered", "id": "urn:uuid:…", "revision_id": "sha256:…"},
    {"role": "joined", "id": "urn:uuid:…", "revision_id": "sha256:…"}
  ]
}
```

Rendering the request again, from the claim, gives the same view of the
same revisions; the bytes may differ where unrecorded software differs, and
the admitted Artifact is the evidence. The claim makes no statement about
the music: it relates a file to what it was made from, as PROV's
`wasDerivedFrom` (ADR 0012), used as a mapping only. It names no recipient;
the disclosure record does.

**The rules live in World until a release installs them.**
`world/exports.py` holds the export request and claim rules, the export
key and file layout, the practice request, key, layout and position, the
practice decision, transposition, and the `<harmony>` mapping, with only
the standard library. Door cannot admit an export claim until a specification
release installs its bundle; the release adds the Door rules this record
needs: a key recomputed from the payload and references in order, a media
type fixed by the format, option values that are booleans, integers, or
short text, and distinct pins.

**New nouns.** *Practice render*, *practice request*, *practice key*,
*transposition*, *export*, *export request*, *export key*, and *loss* enter
the [vocabulary](../vocabulary.md). None is a subsystem: a practice render
and an export are derived files like derived media, a transposition is a
view parameter, and the export claim is a claim. They own no verb beyond Enrichment's
*derive*, enforce no new invariant, and hold no authoritative state.

## Consequences

- A bassist opens a song, mutes the bass, slows it to 75 percent, and loops
  a passage: the stems come from the bank by the recording's digest, the
  mix is the client's or a render's, the loop is the application's own, and
  the archive changes not at all.
- Stems are made only where someone ran `stems`; a recording without them
  lists every stem as missing, and a practice request is refused with
  `stems_not_made`. Whether the import chain separates every recording is
  the operator's choice (a recording-hour of separation takes most of an
  hour).
- Renders are made only when asked for, one file per distinct setting, so
  the derived-media root grows with use: 16-bit stereo FLAC at 44,100 Hz is
  roughly 300 to 400 MB a recording-hour. These are estimates; the first
  real renders should be measured. Nothing prunes them; they may be deleted
  at any time.
- A client that mixes live fetches each stem it plays: FLAC at about 700 to
  900 kb/s a stem, four to five times the listening copy's 192 kb/s, so four
  stems on a phone cost far more than one render. Which path is better on a
  phone is for testing to show (D11.5); a lossy practice copy would be
  another renderer version.
- An export sent to a bandmate stays exactly what they received, beside the
  claims that later correct what it showed.
- No invariant changes. INV-7 keeps every claim an export rendered; an
  export never ranks them.

## Rejected

- **A practice extractor with fixed outputs** (the recording with one stem
  left out, as four bank features). D11.3 asks for any subset with gains,
  which is too many outputs to make in advance, and a bank feature is made
  by an extractor run, not on request.
- **Mixing on the client only, or on the server only.** D11.5 keeps both:
  the client answers at once, and a render costs one stream instead of
  four. Which wins is for testing.
- **Stems and mixes admitted as Artifacts by default.** Gigabytes of
  regenerable bytes would become permanent evidence, every copy policy would
  count them, and destroying a recording would have more to find.
- **A sensitivity on each stem.** A stem is the recording; a second label
  could only disagree with the first, and the weaker one would leak.
- **Excerpts cut by the relay.** Ranges already reach any passage; a cut
  excerpt is another derivative with its own key, worth a decision when a
  client needs one.
- **Exports as claims, or every export admitted.** Most renders are looked
  at once; admitting each would store a regenerable view as evidence.
- **Keying an export by its own bytes.** The key must be known before
  rendering, and an engraver's version changes bytes without changing what
  was rendered.
- **A derivation run as the export's record.** A run's outputs are claims
  that pin it (ADR 0064); nothing in a run says which Artifact it made, and
  a run's times and inputs are not what a recipient's file needs.
- **Recipients in the export claim.** Who received content is a disclosure,
  recorded once by its own mechanism (ADR 0016); repeating it in the claim
  would make two records that can disagree.
- **Practice state in the archive.** Loops and progress change every minute
  and say nothing about the music; as claims they would bury the evidence
  they sit beside.

## Amendment, 2026-10-20: open repeats, last-time endings, and stacked verses

Made on the acceptance of [ADR 0113](0113-editing-lead-sheets.md), which
gives the reasons. The text above is left as it was accepted; where it
disagrees with this section, this section holds.

- The MusicXML and LilyPond exporters write a form's repeats, endings and
  jumps; an open repeat with no `times` and its instruction as text; a
  last-time ending; and stacked verses as numbered lyric lines, one per
  pass an underlay names.
- An exporter refuses a sketch (a melody with an unmeasured item).
- The losses gain an open repeat's played count and a folded chart's
  variant passes.

## Amendment, 2026-10-22: the `ireal` format and written directions

Made on the acceptance of [ADR 0116](0116-ireal-charts.md) (decisions D and
E), which gives the reasons, and installed by specification 0.0.11 (D-19).
The text above is left as it was accepted; where it disagrees with this
section, this section holds.

- The format table gains a row:

  | Format | Media type | Suffix | What it renders |
  |---|---|---|---|
  | `ireal` | `text/plain` | `.irealb` | an `irealb://` link of one chart |

  A sent file in that format is admitted under `archive.claim/export`
  version 2 (version 1 plus `ireal`); version 1 still names the four formats
  above. Exporter `lead-sheet-ireal` version 1 (`documents/ireal/export.py`)
  writes it.
- The MusicXML and LilyPond exporters write an untimed bar's written
  directions (meter layers version 3): MusicXML as a `<direction>` with
  `<words>` and, for a tempo, a `<metronome>` and no `<sound tempo>`;
  LilyPond as `\tempo` for the first and `\mark` for the rest, a fractional
  tempo rounded and listed as a loss.
