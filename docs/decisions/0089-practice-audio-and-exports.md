# 0089. The relay serves bank audio for practice; exports are views until shared

Status: proposed

A person learning a song from a lead sheet wants to hear it without their
own part: the recording minus the bass, for the bassist; the vocal alone, to
learn a line. They also want the lead sheet on paper or in a notation
program, and to send it to a bandmate. Three things stand in the way:

- The stems already exist. The `stems` extractor separates a recording into
  `vocals`, `drums`, `bass`, and `other` as 16-bit FLAC `file` features in
  the feature bank ([ADR 0075](0075-stems-as-content.md)). But a feature is
  never admitted ([ADR 0073](0073-feature-bank.md)), and the relay serves
  only an Artifact's derived media, peaks and a listening copy, and only to
  the active operator ([ADR 0069](0069-derived-media.md)). Nothing makes a
  mix with one stem left out.
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
- **Implied**: practice state (loops, progress) is application state, not
  archival ([ADR 0054](0054-door-service-for-applications.md)).

## Decision

### Practice audio

**A practice mix is a feature, made by an extractor.** `practice`, version
1, needs `stems/vocals`, `stems/drums`, `stems/bass`, and `stems/other`, and
writes four `file` features, each the recording with one stem left out:

| Output | Sum of |
|---|---|
| `without_vocals` | drums, bass, other |
| `without_drums` | vocals, bass, other |
| `without_bass` | vocals, drums, other |
| `without_other` | vocals, drums, bass |

Each is 16-bit FLAC at the stems' 44,100 Hz stereo: the integer sum of the
three stems' decoded samples, clamped at full scale and encoded with
`ffmpeg`'s bit-exact flags, as `stems` encodes its own files. Integer
arithmetic on the same stems gives the same bits, so a key means one file.
Beside each it writes `<output>_clamped`, a scalar counting the samples
clamped. It runs in the archive's environment (numpy and `ffmpeg`); it never
loads the separator. Since it needs the stems, its key holds the stems
entry's key (ADR 0073), so stems made by another separator or other weights
are other practice mixes. Its entry is the recording's, filed under the
recording's directory, and destroying the recording deletes it with the
stems ([ADR 0075](0075-stems-as-content.md)).

A stem alone is already a practice track ("the vocal alone"), so the
extractor writes no solo outputs. Mixes of two stems, or other gains, are
another version or other parameters when someone needs them.

**The relay serves configured audio features by recording digest and
feature name.** Two routes are added beside ADR 0069's:

- `GET /media/{digest}/bank/{extractor}/{output}` answers the bytes of one
  `file` feature of the content `{digest}`, such as
  `/media/sha256:<hex>/bank/stems/bass` or
  `/media/sha256:<hex>/bank/practice/without_bass`, with byte ranges, the
  output's declared media type, an entity tag naming the entry's key and the
  output, and caching that is private and immutable. A media element seeks
  within a long recording by ranges; the relay cuts no excerpts.
- `GET /media/{digest}/practice` answers, in the relay's JSON envelope
  ([ADR 0060](0060-relay-interface-and-role.md)), which of the configured
  features exist for that content, so a client can offer only those:

```json
{
  "ok": true,
  "archive": {"archive_id": "…", "spec": "archive/0.0.10", "role": "scratch", "projections": [6]},
  "result": {
    "content": "sha256:<recording hex>",
    "features": [
      {"name": "stems/bass", "media_type": "audio/flac", "bytes": 31457280,
       "sha256": "sha256:<stem hex>", "key": "sha256:<stems entry key>"},
      {"name": "practice/without_bass", "media_type": "audio/flac", "bytes": 52428800,
       "sha256": "sha256:<mix hex>", "key": "sha256:<practice entry key>"}
    ],
    "missing": ["stems/vocals", "practice/without_vocals"]
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

A stem or a practice mix is the recording (ADR 0075). It takes the
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
stems and mixes of that recording are the same content in other forms, so
the relay records nothing per request, and logs the caller's agent, never
the login (ADR 0060). A disclosure record may overcount and never
undercount; it is not made again for every range a player fetches.

**Never an Artifact unless a person chooses.** A stem or a practice mix stays
in the bank, regenerable and deletable at any time. A person who wants one
kept as evidence, for example because it was sent to someone outside the
relay, admits it as ADR 0079 states for a chosen stem, citing its bank
reference. The relay admits no content (ADR 0056), and ADR 0085's upload
route refuses audio, so that admission is an operator tool's.

**Practice state is application state.** Loops (a start and end sample),
speed, pitch shift, which mix is playing, which stems are muted, and how
often a passage has been played are a practice application's own state,
kept where it keeps its own (browser storage, or its own store). None goes
through Door or into World; losing it loses nothing archival
([state model](../state-model.md)). A loop that a person wants others to
find ("this bar is hard") is not practice state but an annotation on a span,
a claim under ADR 0081.

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
  the lead sheet's revision (ADR 0084), which already pins the exact chord,
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
and the joined documents' own sensitivity and rights (ADR 0085). A document
licensed for personal use only (bought tablature, for example) may be joined
in an export for the operator and never in one for anyone else. A request
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
sheet sent beyond the band needs every co-writer's approval first
(decision D12.2, ADR 0085). A PDF, MusicXML, LilyPond, or JAMS file is a
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
`world/exports.py` holds the request and claim rules, the key, the file
layout, the practice decision, and the `<harmony>` mapping, with only the
standard library. Door cannot admit an export claim until a specification
release installs its bundle; the release adds the Door rules this record
needs: a key recomputed from the payload and references in order, a media
type fixed by the format, integer-only option values, and distinct pins.

**New nouns.** *Practice mix*, *export*, *export request*, *export key*,
and *loss* enter the [vocabulary](../vocabulary.md). None is a subsystem: a
practice mix is a feature, an export is a derived file like derived media,
and the export claim is a claim. They own no verb beyond Enrichment's
*derive*, enforce no new invariant, and hold no authoritative state.

## Consequences

- A bassist opens a song, picks `without_bass`, and loops a passage: the
  audio comes from the bank by the recording's digest, the loop is the
  application's own, and the archive changes not at all.
- Stems are made only where someone ran `stems`; a recording without them
  lists every practice feature as missing. Whether the import chain
  separates every recording is the operator's choice (a recording-hour of
  separation takes most of an hour).
- Each practice mix is a full-band signal: 16-bit stereo at 44,100 Hz is
  635 MB a recording-hour before compression, and FLAC is expected to take
  roughly half to two thirds of that on music, so four mixes add about
  1.3 to 1.7 GB a recording-hour to the bank, beside the stems. These are
  estimates; the first real mixes should be measured, as ADR 0075 asks of
  stems. Nothing prunes them; they may be deleted at any time.
- FLAC at about 700 to 900 kb/s is four to five times the listening copy's
  192 kb/s. A song is tens of megabytes; a client that plays a long
  rehearsal's stem fetches by ranges. A lossy practice copy would be
  another output version if this proves too slow on a phone.
- An export sent to a bandmate stays exactly what they received, beside the
  claims that later correct what it showed.
- No invariant changes. INV-7 keeps every claim an export rendered; an
  export never ranks them.

## Rejected

- **Practice mixes as derived media.** Derived media are named by a fixed
  method of one content (ADR 0069); a practice mix is made from the
  separator's output, so its name would have to carry the stems entry's key
  and parameters, which is what a bank key is for (ADR 0073).
- **Mixing on the client only.** A client may still mix stems itself (to
  mute two at once), but four FLAC streams kept in step on a phone cost four
  times the bandwidth of one mix, and the common case is one stem out.
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
