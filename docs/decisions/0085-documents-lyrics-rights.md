# 0085. Documents arrive through the relay; lyrics are timed by reference; sensitivity and rights are claims

Status: proposed

A lead sheet joins chords, meter, form, melody, and lyrics. Specification
0.0.9 holds chords and meter ([ADR 0077](0077-chord-stacks-and-meter.md)),
and a chord sequence already places chords at a document's line and word
without copying the words. Nothing places a lyric line on a recording's
samples, so an application cannot follow the words while a recording plays,
and an ASR transcript, an LRC file, or the operator's own typed lyrics have
no contract to land in. The V2 transcription unit was not ported
([cutover plan](../proposals/cutover-plan.md)).

The documents themselves cannot reach the archive from an application. The
relay refuses an admission whose manifest lists content
([ADR 0056](0056-tailnet-relay.md)), so a lyric file, a chart, or an export
of a Google Doc can be admitted only by an operator tool on the archive
machine.

And nothing yet says who may see what. [ADR 0016](0016-disclosure.md) makes
sensitivity a claim in Traffic Light Protocol labels, [ADR 0018](0018-people-in-the-archive.md)
lets the people a recording records set their own limit, and
[ADR 0010](0010-publication.md) makes rights claims with terms; but no
contract holds either claim, and the relay serves everything to the one login
it allows. Bandmates are about to read the archive (ADR 0088), and a paid
tablature or a private conversation between takes must not reach them by
accident.

The operator decided the following on 2026-10-18; this record states it as
given.

- **D7.1** A timed lyric-line claim places lines and words of a lyric
  document at sample spans of a recording, and never copies the words. It
  covers the operator's own documents, LRC files, and ASR transcripts.
  Transcripts become document Artifacts.
- **D7.2** A relay route stages a document's bytes for admission, refusing
  audio and video, for the operator's login only at first. Google Docs come
  in as dated exports, each its own Artifact, with the URL kept as source.
- **D7.3** Minimal sensitivity (operator, circle, published) and rights
  (RightsStatements.org, and "licensed for personal use", such as a paid
  tablature) contracts land in 0.0.10. The relay and the applications enforce
  them before showing or sharing anything.

## Decision

Four contracts, each version 1, are added by specification 0.0.10:
`archive.claim/document_source`, `archive.claim/timed_lyrics`,
`archive.claim/sensitivity`, and `archive.claim/rights`. Every rule below is
in `world/documents.py`, which uses only the standard library so that the
relay, an importer, or a client can check a proposal before Door does.

### What a document is, and where it came from

A **document** is an outside source's admitted bytes
([vocabulary](../vocabulary.md)): an Artifact like any other. Its payload
holds only its content digest and size (D-8), so what kind of bytes it is,
and where they came from, is a claim about it.

**`archive.claim/document_source` version 1** pins exactly one `document`,
the Artifact revision it describes; zero to eight `derived_from` Artifact
revisions, the content the document was made from (the recording a
transcript transcribes); zero to sixteen `work` Entity revisions of kind
`musical_work`, the Works it is about; and at most one `run`, a derivation
run version 1 Claim revision, when a machine made the document. Its payload:

```json
{
  "media_type": "text/plain",
  "kinds": ["chords", "lyrics"],
  "url": "https://docs.google.com/document/d/<id>",
  "retrieved_at": "2026-10-18T09:30:00Z",
  "filename": "Untitled song.txt"
}
```

- `media_type` (required) is one of the document types below.
- `kinds` (optional) is 1 to 10 distinct values in ascending order from
  `booklet`, `catalogue_response`, `chords`, `licence`, `lyrics`, `notes`,
  `other`, `score`, `tablature`, and `transcript`. A `transcript` pins its
  `run` and at least one `derived_from`.
- `url` (optional) is where the bytes were retrieved, an `http` or `https`
  URL of at most 2,000 characters in the characters RFC 3986 allows, as D-17
  states `document_url`. A claim with a `url` gives `retrieved_at`, an RFC
  3339 full date or UTC instant. `retrieved_at` may also stand alone, as the
  date a file was saved.
- `filename` (optional) is the name the file was given, 1 to 255 characters,
  never a path. It never identifies anything ([ADR 0027](0027-core-payloads-and-installed-contracts.md)).

As in [ADR 0042](0042-work-correspondence.md), the URL says where the bytes
came from and the Artifact is the evidence.

**Google Docs are dated exports.** A Google Doc changes in place, so the
archive never refers to "the document at this URL". Each export is its own
Artifact with its own `document_source` claim, whose `url` is the document's
canonical URL, `https://docs.google.com/document/d/<id>`, whichever view
or export URL it was copied from, and whose `retrieved_at` is the instant of
the export. Door refuses another form of a Google Docs document URL in that
field. The exports of one Google Doc are the Artifacts whose source claims
share that URL, ordered by `retrieved_at`. An export whose bytes equal an
earlier export's is the same content and admits nothing new. A plain-text
export (`text/plain`) is the one a timed lyrics claim can locate; the
operator may also keep a PDF or Word export of the same moment, as its own
Artifact.

**Transcripts are documents.** An ASR run's transcript is admitted as a
plain-text document Artifact, one line per segment, with a
`document_source` claim that pins the run and `derived_from` the recording
it transcribed, and a `timed_lyrics` claim, asserted by the run, that places
its lines and words on that recording. The words live once, in the document;
the times live once, in the claim. A transcript is lyrics only when a person
says so: by asserting a `kinds` that includes `lyrics`, or by timing it
against a Work.

### Timed lyrics

**`archive.claim/timed_lyrics` version 1** pins exactly one `document`, the
lyric document's Artifact revision; exactly one `source`, the audio
Artifact revision whose samples it counts; exactly one `stream`, an
`archive.claim/audio_stream` version 1 Claim revision about that same
Artifact revision; at most one `work`, a `musical_work` Entity revision;
at most one `run`, which a machine's claim pins; and zero to eight
`corrects`, `archive.claim/timed_lyrics` version 1 Claim revisions it
corrects. The `source` is the mix, as for chord stacks; a run that listened
to a separated vocal stem records the stem among its inputs, by its bank key
(ADR 0079).

```json
{
  "sample_rate": 48000,
  "scheme": "text",
  "alignment": {"method": "manual"},
  "lines": [
    {"line": 1, "start_sample": 576000, "end_sample": 720000,
     "words": [
       {"word": 1, "start_sample": 576000, "end_sample": 595200},
       {"word": 2, "start_sample": 595200, "end_sample": 624000}
     ]},
    {"line": 2, "start_sample": 744000, "end_sample": 912000},
    {"line": 1, "start_sample": 2880000, "end_sample": 3024000}
  ]
}
```

- `sample_rate` equals the stream's, and no span ends after its
  `sample_count`.
- `scheme` says how the document is counted: `text` or `lrc` (below).
- `alignment` (required) is how the document's lines were placed on the
  samples: `method` (1 to 200 characters; `manual` for a person tapping
  along, `lrc timestamps` for an LRC file's own times) and optional
  `max_error_samples`, at least 0. A lyric document has no samples of its
  own, so every timed lyrics claim states one.
- `lines` holds 1 to 10,000 objects, each a `line` number (from 1),
  `start_sample` and `end_sample` with `0 <= start < end`, an optional
  `end`, and optional `words`. Lines are in order of start, and may overlap:
  two voices sing at once. A line sung twice is placed twice.
- `words` holds 1 to 200 objects, each a `word` number (from 1) within that
  line, `start_sample`, `end_sample`, and an optional `end`. Words are within
  their line's span, in order, never overlapping, with strictly increasing
  numbers.
- `end` is `asserted` (also when absent) or `held`, as in a chord stacks
  span: an LRC file gives each line's start and the asserter holds it to the
  next. The payload states `hold_rule` (1 to 500 characters) exactly when
  some end is held.
- `confidence_scale` (1 to 200 characters) names what a machine's
  confidence means; then every line and word carries `confidence`, exact
  decimal text, and otherwise none does. A confidence compares only within
  its claim (INV-8).

**Locators, exactly.** The document is UTF-8, with an optional byte order
mark. A line ends at LF, CR LF, or a lone CR, and a final line break starts no
further line; `line` 1 is the first line. A word is a maximal run of
characters without the Unicode `White_Space` property; `word` 1 is a line's
first. Under `lrc`, LRC time tags (`[mm:ss.xx]` at a line's start and
`<mm:ss.xx>` within it) are not words, and line numbers still count every
line of the file, its tag lines included. Door does not read content, so it
cannot check that a numbered line exists; `check_locators` does, for an
importer or a view, and a locator that misses is an error there, never an
empty line.

**The words stay in the document.** No field of the claim holds a word, and
an unknown field is refused, so a client cannot slip a line's text into
one. This is ADR 0077's rule for chord sequences, for the same reason: the
words are the document's, under the document's own sensitivity and rights,
and a claim copied from them would carry them past both. The words are
joined only when shown (`lyric_view`), from document bytes the viewer may
see (below). The joined lines are a view, never stored.

**One document, several placements.** A lyric document may be placed on many
recordings, each placement its own claim, and on one recording by several
asserters. A correction is its own claim citing in `corrects` what it
corrects, as in ADR 0077; how the correction overlay shows it is
ADR 0082's. When a Google Doc is
exported again and its lines move, the earlier claims keep pointing at the
earlier export, which is unchanged; a placement against the new export is a
new claim.

**Untimed lyrics stay in the document.** A Work's lyrics without a recording
need no claim of this contract: the document is the lyrics, its
`document_source` names the Work, and a chord sequence locates its chords at
the document's lines and words. A lead sheet
(ADR 0084) pins the document Artifact for untimed
lyrics and a `timed_lyrics` claim for timed ones.

### The relay stages documents

**`POST /documents/stage`** takes `{media_type, content, filename?}`, with
the bytes as base64 in `content`. It is a JSON body because ADR 0056's
defence against other pages relies on every write being
`application/json`; a raw `text/plain` body is one a page on another origin
may send without asking. The relay:

1. refuses any caller but the active operator's person (`operator_only`).
   Bandmates who read and assert under ADR 0088 do not stage content;
   opening the route to them is a later decision;
2. decodes the bytes, up to 64 MiB;
3. refuses audio and video (`content_audio_or_video`): a declared
   `audio/*` or `video/*` type other than `audio/midi`, and bytes that begin
   like an audio or video container or stream (RIFF WAVE or AVI, AIFF, ISO
   base media such as MP4 and M4A, FLAC, Ogg, MP3 and ADTS frames, Matroska,
   ASF, MPEG program and transport streams, and others listed in
   `audio_or_video`), whatever type they are declared as. A zip-based
   document whose members include such bytes is refused too;
4. refuses a type not on the list below (`media_type_unsupported`), and bytes
   that are not what their type says (`content_mismatch`): text that is not
   UTF-8 or holds a NUL, a PDF without its header, a zip document without its
   marker entry;
5. adds the bytes to the staging git-annex repository on the archive machine
   ([ADR 0023](0023-admission-and-staging.md), [ADR 0070](0070-capture-import.md))
   and answers the row an admission manifest's `contents` needs (`digest`,
   `size`, `annex_key`, `staging_remote`), the canonical media type, and,
   when the content is already an Artifact's, that Artifact.

The document types are `text/plain` (lyrics, LRC, ChordPro, transcripts,
plain-text exports), `text/markdown`, `application/json` (a saved catalogue
response), `application/pdf`, `application/rtf`, Word (`.docx`) and
OpenDocument text, MusicXML (`+xml` and compressed `.mxl`),
`application/x-guitar-pro` (versions 3 to 8), `audio/midi`, `image/png`, and
`image/jpeg` (a scanned booklet or a photographed chart). It is an
allowlist: anything else is refused until a decision adds it.

**Staging admits nothing.** The client then sends an ordinary admission
through `/door/admit`. This record amends ADR 0056 so that the relay passes
an admission whose manifest lists content when, and only when, every
content row is bytes this relay staged for the same caller, and every
Artifact carrying them is described in the same batch by a
`document_source` claim stating the media type it was staged as
(`check_document_admission`). Any other content admission is still refused
as `content_not_relayed`; one whose staged Artifact is undescribed or
described as another type is `content_not_described`. Audio still enters
only through the importer ([ADR 0070](0070-capture-import.md)). Door checks
everything it checks for any content admission. Staged bytes that are never
admitted are operational state: the relay reports them, and they are removed
after seven days.

The relay still owns no archival state; the staging repository is
operational, and losing it loses only what was not yet admitted.

### Sensitivity

**`archive.claim/sensitivity` version 1** is the claim ADR 0016 describes.
It pins 1 to 1,000 `subject` revisions of distinct objects, of any shape,
and no `run`: a sensitivity is a person's, never a machine's. Its payload is
`{label, reason?}`, where `label` is a Traffic Light Protocol 2.0 label,
`RED`, `AMBER`, `GREEN`, or `CLEAR`, and `reason` is 1 to 2,000 characters.
Its envelope `agent` is the person whose limit it is: the operator for their
own, or the person the content records (ADR 0018), recorded by the operator
with that person as its author. [ADR 0088](0088-bandmates.md) keeps
sensitivity claims the operator's to send, so a bandmate with a login does
not assert one through the relay in 0.0.10; whether they should, for limits
that only narrow, is open.

The three settings of D7.3 are three of those labels, so nothing new is
invented and ADR 0016's table is unchanged:

| Application setting | Label | Furthest class ([ADR 0010](0010-publication.md)) |
|---|---|---|
| operator | RED | private: the operator only |
| circle | AMBER | circle: people chosen one by one |
| (not offered) | GREEN | service: a processor, but no one else |
| published | CLEAR | open and distribution |

`GREEN` stays admissible because ADR 0016 needs it for processors: a
recording that may go to a hosted transcription service. Service is further
out than circle, so a `GREEN` object may also be shown to the circle.
Applications offer the three settings, and show a `GREEN` claim made
elsewhere as "circle".

A label applies to the object it names, every revision of it, so revising
an object never escapes its label. For each person and object, that
person's latest sensitivity claim about the object, by Door's recording
time, is their limit: so only they lift it, by a later claim, as ADR 0018
requires, and the operator's later `CLEAR` does not lift a bandmate's
`RED`. The strictest limit of all persons wins. A judgment changes no
limit.

### Rights

**`archive.claim/rights` version 1** is one rights statement with its terms.
It pins 1 to 1,000 `subject` revisions of distinct objects, each an Artifact
or an Entity of kind `musical_work`, `recording`, or `release`, since a
composition and a recording of it have separate rights (ADR 0010); zero to
eight `holder` person or organisation Entity revisions; at most one
`licensee`, a person; at most one `document` (the licence, a receipt, a
terms page); and at most one `run`. Its payload:

```json
{
  "statement": "personal-use-licence",
  "from": "2026",
  "text": "for personal, non-commercial use only",
  "document_url": "https://tabs.example/receipt/1",
  "retrieved_at": "2026-10-18"
}
```

- `statement` (required) is a RightsStatements.org statement, by its
  identifier (`InC`, `InC-OW-EU`, `InC-EDU`, `InC-NC`, `InC-RUU`, `NoC-CR`,
  `NoC-NC`, `NoC-OKLR`, `NoC-US`, `CNE`, `UND`, `NKC`; its URI is
  `http://rightsstatements.org/vocab/<id>/1.0/`), or the one local statement,
  `personal-use-licence`: the subject was licensed to the `licensee` for
  their own use, as a paid tablature or chart is. A claim names a
  `licensee` exactly when its statement is `personal-use-licence`.
- `jurisdiction` (optional) is an ISO 3166-1 alpha-2 code.
- `from` and `until` (optional) are partial dates (`YYYY`, `YYYY-MM`, or
  `YYYY-MM-DD`): the term, such as a licence period or the year a copyright
  ends. The latest day `until` can mean is not before the earliest day `from`
  can mean.
- `text` (optional, 1 to 2,000 characters) is the terms as written.
- `document_url` and `retrieved_at` describe the pinned `document`, and are
  allowed only with one, as in ADR 0078.

The specification release ships the statements as a SKOS scheme whose
concepts are each `skos:exactMatch` the RightsStatements.org statement;
`personal-use-licence` has no match.

### Reach, and what the relay and applications enforce

**The reach of an object** is how far it may be shown or shared, computed by
World and never stored (`reach` in `world/documents.py`). It is read over the
object and everything it pins, transitively, through every revision of each
(the *closure*), and from a document to what its `document_source` says it
was made from. So a chord claim, a timed lyrics claim, or a lead sheet that
pins a recording carries that recording's limit, and a transcript carries
the limit of the recording it transcribed.

- **Sensitivity:** the furthest class is the strictest label's, over every
  object in the closure. No label means no limit from sensitivity.
- **Rights:** every Artifact, Work, recording, and release in the closure
  needs a rights claim that permits a class beyond service, or it goes no
  further than service (INV-21). A statement `InC` or `InC-*` whose
  `holder`s include the active operator permits distribution; `NoC-US`
  permits distribution; `NoC-NC` permits open; every other statement
  permits service, as does a permitting statement outside its `from` and
  `until` on the day it is read. The strictest claim about each object wins,
  whoever asserted it. Rights never limit the circle, except that
  `personal-use-licence` keeps its subject, and everything that pins it, to
  the operator and the licensee.

**`may_show(object, viewer, audience)`** answers whether the relay or an
application may show an object to a person reaching them as an exposure
class. The operator is always shown everything: no limit takes the
operator's own memory from them (ADR 0018). Anyone else is shown an object
only when the audience is within both limits, and, under a personal-use
licence, only when they are its licensee and the audience is no further than
the circle. A true answer is necessary, never sufficient: the person must
also be granted the disclosure (INV-25), which ADR 0088's circle rules
decide. The relay checks it on every route that returns content or derived
media (ADR 0088, ADR 0089), and
before admitting a publication approval, once publication is built
(cutover condition 8); an application checks it before offering to share.

**The relay serves document bytes.** `GET /documents/{digest}` answers with
an admitted document's bytes, with its `document_source` media type, when
the digest is a document Artifact's content in World's selected generation
and `may_show` allows the caller. This amends ADR 0069's "never serves an
original's bytes" for documents only; it never serves an Artifact that a
`document_source` claim does not describe, and never audio or video. An
application shows timed lyrics by joining the words from these bytes.

**New nouns.** Timed lyrics, locator scheme, document source, reach, and
personal-use licence enter the [vocabulary](../vocabulary.md). None is a
subsystem: each names part of a claim, or a view of claims. They own no verb
beyond Annotation's *assert*, Enrichment's *derive*, and Door's *admit*,
enforce no new invariant (reach is how INV-21, INV-25, and INV-26 are applied
to what the relay shows), and hold no state outside the claims Door admits.

## Consequences

- The operator can type lyrics in a Google Doc, export them, and time them
  against any number of recordings from a phone, and a bandmate can follow
  them while practising, without a word of them in any claim.
- LRC files and ASR transcripts land in the same contract as a person's
  tapping, each attributed; a person's timing and a machine's coexist, and a
  correction cites what it corrects.
- A paid tablature can be admitted and used by the operator, and is never
  shown to the circle. A private moment in a rehearsal can be labelled RED,
  and every claim that pins that recording stays private with it.
- Inheritance through pins is strict: a lead sheet pinning a chord claim
  read from a RED recording is RED. To share the lead sheet, the operator
  pins claims from another recording, or the person who set the limit lifts
  it. This errs towards showing less.
- Publishing stays unbuilt. When it is built, nothing reaches open or
  distribution without a permitting rights claim on every Artifact and Work
  it pins, the operator's own included.
- The relay gains one write route, one read route, and five refusal codes:
  `operator_only`, `content_audio_or_video`, `media_type_unsupported`,
  `content_mismatch`, and `content_not_described`. `request_malformed`,
  `body_too_large`, and `content_not_relayed` keep their meanings.
- Raw SPARQL over `/world/query` still answers every projected claim to its
  caller. ADR 0088 keeps it the operator's: a bandmate reads only named
  views filtered by the circle's readable set, so reach is enforced on
  content routes and views.
- No invariant changes.

## Relation to other decisions

- [ADR 0010](0010-publication.md): rights are the claims it describes, with
  RightsStatements.org and terms; INV-21 is applied as reach.
- [ADR 0016](0016-disclosure.md) and [ADR 0018](0018-people-in-the-archive.md):
  sensitivity is their claim, with their labels and their lifting rule; the
  operator's override past a limit (one destination, no further than
  service) is a disclosure record, which this record does not define.
- [ADR 0023](0023-admission-and-staging.md): staging and admission are
  unchanged; the relay is one more staging host.
- [ADR 0056](0056-tailnet-relay.md): amended so that an admission listing
  content staged by the relay is relayed.
- [ADR 0069](0069-derived-media.md): amended so that document originals are
  served, by digest, under reach.
- [ADR 0077](0077-chord-stacks-and-meter.md): the same source, stream,
  alignment, held end, and locator rules; timed lyrics are the lyric sibling
  of a chord sequence's line and word locators.
- [ADR 0078](0078-contributions.md): the same document, URL, and retrieval
  fields.
- ADR 0079: stems a run listened to
  are cited by bank key in the run.
- ADR 0082: the correction overlay and
  named views of timed lyrics.
- ADR 0084: a lead sheet pins timed lyrics claims and
  lyric documents.
- ADR 0086: a melody note may later point
  at a lyric word by the same locator.
- ADR 0088: bandmates' logins, the
  readable set and the disclosure grant that reach is checked beside, and
  the co-writer consent (D12.2) this record leaves to it.
- ADR 0089: bank audio is served under
  the recording's reach.

## Alternatives considered

- **Copying lyric lines into the timed claim.** It is simpler to display, but
  the words would then be in a claim with its own sensitivity, outside the
  document's rights, and a correction to a typo would be a new claim per
  placement. ADR 0077 rejected the same for chord sequences.
- **Admitting LRC or WebVTT as the timed form itself.** Their times are in
  seconds against whatever file the author had, not samples of an archive
  stream, and their words and times are inseparable; an LRC file stays a
  document, and its times become a claim with an alignment.
- **Transcripts as claims holding their text.** The transcript would be
  words in a claim, invisible to rights and to the lyric view. As a document
  it is content, with a digest, and its timing is a claim like any other.
- **A Google Doc as one Artifact revised in place.** An Artifact is content;
  bytes that change are new content. Successive exports sharing a URL keep
  every version, and old placements stay valid.
- **The relay fetching Google Docs itself.** It would need Google
  credentials on the archive machine and a route that reaches the internet;
  the operator's client exports and stages instead.
- **Raw bodies for staging.** `text/plain` is a body another origin may post
  without a preflight, which ADR 0056's origin rule relies on.
- **Three new sensitivity values (`operator`, `circle`, `published`).** ADR
  0016 already chose TLP labels with a class table; three of them are the
  three settings, and a second vocabulary would need a mapping forever.
- **A label on a contract or a kind.** ADR 0016 rejected it; every claim
  kind holds both private and shareable content.
- **Inheriting limits only from the claim's direct pins.** A lead sheet pins
  a chord claim, which pins the recording; one step would let a RED
  recording's chords reach the circle through the lead sheet.
- **Creative Commons licences as rights statements.** RightsStatements.org
  is for works that a licence does not cover, and ADR 0010 chose it. A
  later version may add licences for the operator's own works when
  publication is built.
