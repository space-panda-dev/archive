# 0105. Notes are snapshotted as documents, and their links to the archive are claims

Status: accepted (operator, 2026-10-19); proposed in wave 2 and its open
questions settled the same day

The operator keeps notes in two notebooks outside the archive: an Obsidian
vault (a folder of Markdown files) and a folder of Org files. Notes say
things the archive should keep beside the recordings: "the bridge of
Harbor Light dragged on Tuesday", "try the loop from the second take at
92", a page about a band with a link to its Wikipedia article. A notebook
app, the fourth application to read World and propose to Door, will show
them beside what they talk about. Today nothing lets it.

- **A note changes in place.** [ADR 0085](0085-documents-lyrics-rights.md)
  admits documents, but only through the relay's staging, one file at a
  time, by the operator; and it settles a document that changes in place
  only for Google Docs, as dated exports grouped by URL. A file in a folder
  has no URL, and `document_source`'s `filename` is "never a path", so
  nothing groups the snapshots of one note or says where it lives.
- **Links have no home.** A note links to other notes (Obsidian
  `[[wikilinks]]`, Org `[[file:…]]` and `[[id:…]]` links), to archive
  objects, and to the web. No contract says a note is about a recording,
  a Work, or a sample, and nothing reads the links in the text.
- **Wikipedia is outside every catalogue.** Correspondence covers
  MusicBrainz Works ([ADR 0042](0042-work-correspondence.md)) and
  MusicBrainz and Discogs artists, releases and recordings
  ([ADR 0087](0087-tracklists-and-correspondence.md)). A Wikipedia link in
  a note joins nothing.

The operator decided the following on 2026-10-19 (wave 2 decisions W2.10
and W2.11, and the earlier settled decision on the notebook); this record
states them as given.

- **Notebook (settled earlier).** The archive holds snapshots of org notes
  and an Obsidian vault as document revisions; links as claims;
  Wikipedia/Wikidata through World's catalogue correspondence, promoted to
  claims when kept.
- **W2.10 Notebook links.** Links between notes (Obsidian wikilinks, org
  links) are a view parsed from the text. A link from a note to an archive
  object (a recording, Work or sample) is a claim, asserted by a person or
  proposed by a machine. Wikipedia goes through catalogue correspondence
  extended to Wikidata IDs.
- **W2.11 Note snapshots** come from a watched folder on a schedule set in
  policy (daily by default), with quiet history: only changed notes make
  revisions.

The operator settled this record's open questions on 2026-10-19 (round 6
answers R6.1, R6.4, R6.14 and R6.17; R6.16 settles the rest as
recommended). The decision below states the settled choices; the list at the
end records them.

## Decision

The word *notes* already names note events on a recording
([ADR 0086](0086-notes-and-melody.md), the notes claim), and
`document_source`'s kind `notes` means liner notes. So this record calls a
notebook's file a **page** (what Obsidian and Org call a note), and the
folder it lives in a **notebook**.

### A notebook is a collection; a page is one Artifact with revisions

**The notebook.** Each notebook is an Entity of kind `collection`
([ADR 0100](0100-packs-and-collections.md), W2.3), created once by the
operator with a label ("Field notebook"). No new kind: a notebook is a
personal library of pages, as a sample library is of samples, and ADR 0100's
collection holds items of any media (R6.1). Its pages are its members, given
only by ADR 0100's membership claims, which the snapshot command proposes
(R6.1). Where its folder is on disk is configuration, never a record
(S-9).

**The page.** Each page is one Artifact object. Its first snapshot is the
object's first revision; each later snapshot whose bytes differ is a new
revision that supersedes the head. This is what "document revisions" means:
S-9 keeps an object's identity when its content changes, and ADR 0003's
policy revision is the precedent of an Artifact revised in place. One
object per page gives a sensitivity label that covers every revision
(ADR 0085: a label applies to the object and every revision of it), a
stable target for links and backlinks, and a page history read straight
from its supersession chain. Google Docs exports keep ADR 0085's separate
Artifacts; nothing here changes them.

**What each revision carries.** A snapshot of a changed page admits, in
one batch:

1. the Artifact revision (payload: its content only, D-8);
2. an `archive.claim/document_source` version 1 claim describing that
   revision: `media_type` `text/markdown` for a `.md` page or `text/plain`
   for a `.org` page (Org has no type on ADR 0085's list, and plain text is
   what it is), `filename` the file's base name, `retrieved_at` the
   snapshot instant, no `kinds` (Settled 6);
3. when the page is new or has moved, the **source-path claim** of
   ADR 0100 (W2.4), pinning the page revision and, as `within`, the
   notebook Entity, with the page's path relative to the notebook's folder
   (`Rehearsals/2026-10-14.md`). The path is evidence of where the page
   lived, never its identity, and never what makes it a member;
4. when the page is new, its **membership** in the notebook: ADR 0100's
   `archive.claim/collection_members`, revising the notebook's latest
   membership claim to add the page while it lists fewer than 1,000
   members, or a new membership claim when it is full (R6.1).

The page Artifact, its document source, its source-path claim and its
membership name the active operator as `agent` (it is the operator's own writing) and the snapshot command's
Package URL as `by`. No origin claim ([ADR 0096](0096-origin-of-recordings.md))
is proposed: origin is about recordings and audio content, and a page with
none is unknown origin beyond the circle, which is never wider; origin is
decided when publishing a page beyond the circle is first wanted
(Settled 5).

### Snapshots: a schedule in policy, quiet history

**The command.** A new `capture notebook` command, beside `capture import`
([ADR 0070](0070-capture-import.md)), snapshots one notebook. Like the
importer, it reads the archive through `archive export`, proposes through
`door admit` on the archive's machine, and never changes its input. A
notebook kept on another machine reaches it as a synced folder or through
`capture send`.

1. It lists the notebook's pages: files ending `.md` or `.org` under the
   folder, skipping dot-directories (`.obsidian/`, `.trash/`, `.git/`) and
   any exclusion configured for that notebook.
2. For each page path it finds the page object whose head source-path claim
   names this notebook and path, and that object's head revision.
3. **Quiet history (INV-5).** A file whose digest equals the head
   revision's content proposes nothing. A changed file proposes a new
   revision and its document source. A new path proposes a new page
   object, its document source and its source-path claim. A run over an
   unchanged notebook writes nothing at all, so a daily run of a quiet
   notebook leaves no trace.
4. **Moves.** A page whose path vanished while a new path appeared with
   the same digest in the same run is the same page moved: the command
   proposes only a new source-path claim. When a page states its own
   stable identifier (an Org file-level `:ID:` property, an Obsidian front
   matter `id`), the command follows it across a move even when the text
   changed too; the identifier finds the object, as `corresponding` finds
   an Entity by its external URI, and never becomes the object's identity
   (S-9). A move with changed text and no identifier is a new page.
5. **Removals.** A page deleted from the folder is recorded by revising
   the membership claim that listed it, without it, at that snapshot,
   ADR 0100's rule for removing a member (R6.1, Settled 2). So "deleted"
   and "unchanged" no longer look alike. The page stays in the archive with
   its history and last path, and is never retired, which is the
   operator's act of taking something out of the archive.

Between two snapshots a page may change many times; only its state at each
snapshot is kept. That is the cost of a daily cadence, and the reason the
cadence is the operator's to set.

**The schedule is desired state (W2.11).** Policy says which notebooks are
snapshotted and how often: "the field notebook is snapshotted at least
daily". A notebook with no rule is snapshotted daily, the decision's
default. Configuration maps each notebook Entity to its folder, as it maps
derivation kinds to tools ([ADR 0007](0007-policy.md)). Plan turns the rule
into a standing desire for that notebook with its cadence; apply runs
`capture notebook` when the cadence has passed since its last run. The
time of the last run is operational state, like a routine verification
result ([ADR 0008](0008-plan-and-apply.md)): policy cannot read it from the
records, because quiet history leaves no record of a run that found
nothing, and does not need to, because running early changes nothing. Until
the policy language exists ([open](../open-questions.md#policy)), a timer
runs the command daily.

**The planned amendment of ADR 0007 (R6.14).** ADR 0007 lists policy's
desires as placement, derivation, review requirements and publication, and
admitting an outside folder on a cadence is none of them. When this record
is accepted, ADR 0007 is amended to add a fifth kind, the **snapshot
desire**: which outside sources (in wave 2, notebooks) are admitted, and how
often. Its cadence is a standing desire that plan reads; the time of the
last run is operational state, never a record; the folder a source maps to
is configuration (mechanism), never policy. The amendment adds no verb:
apply carries the desire out through `capture notebook` and Door's *admit*.
The amendment was made in ADR 0007 on this record's acceptance
(2026-10-19).

### `archive.claim/notebook_link` version 1

A **notebook link** says a page is about an archive object (W2.10). It maps
to `dcterms:references` from page to target, as a mapping only
([ADR 0012](0012-inherited-meaning.md)).

| Role | Count | Pins |
|---|---|---|
| `page` | exactly 1 | an Artifact revision: the page revision the link was asserted on or read from |
| `target` | exactly 1 | an Artifact revision (a recording, a sample render, any content), an Entity revision of an installed kind (a `musical_work`, `recording`, `recording_session`, `release`, `collection`, person or organisation), or an `archive.claim/audio_selection` Claim revision of version 2 or later (a cut or sample, [ADR 0099](0099-samples.md)) |
| `run` | 0 or 1 | a `derivation_run` version 1 Claim revision, when a machine read the link |

Payload, every field optional:

```json
{
  "locator": {"line": 12, "start_char": 4, "end_char": 61},
  "at": {"start_sample": 8467200, "end_sample": 8908200, "sample_rate": 44100}
}
```

- `locator` is where in the pinned page revision the link is: a `line`
  from 1, counted as `document_lines` counts lines (ADR 0085), and
  optionally `start_char` and `end_char`, Unicode code points within the
  line, `0 <= start_char < end_char`. No locator means the page as a whole.
  The link never copies the page's words.
- `at` is a half-open span of integer samples with its rate, as a
  selection's spans are (S-15), on a `target` Artifact: "at 3:12 in
  Tuesday's take". A longer-lived excerpt is a cut, linked as a selection.

**Door rules** (a specification release adds them with the bundle): the
counts and kinds above; `page` and `target` name different objects; a
`locator` with `line >= 1` and, when given, both character bounds in order;
`at` only on an Artifact target, with `0 <= start_sample < end_sample` and
a positive rate; an agent that is a person, or a process with a `run`.
Door does not read source-path claims or bytes, so World's check (not Door)
refuses a link whose `page` is not a page of a notebook, and one whose
`target` is itself a page: links between pages are W2.10's view, not
claims.

**Who asserts it.**

- **A person**, in the notebook app ("this page is about that take"). It
  falls under the `annotate` ability ([ADR 0094](0094-peoples-writes.md)):
  a claim about readable things, so the relay adds it to the member
  contracts under that grant and its rule that every pin be readable
  applies. `cut` and `admit` do not apply; a member cannot snapshot a
  notebook in wave 2, and a friend with `admit` deposits Markdown as
  ordinary documents, without revisions or a notebook (Settled 4). The
  notebook link under `annotate` is part of the one ADR 0094 amendment that
  lists every wave-2 ability addition (R6.10).
- **A machine.** A link reader, run as an enrichment over each new page
  revision ([ADR 0006](0006-enrichment.md)), proposes a link for every
  archive object the text names explicitly: an object ID (`urn:uuid:…`)
  alone or inside a link, with an optional media fragment (`#t=192`) read
  into `at`. Its agent is the reader's process Entity and it pins its run.
  It proposes a link only where no head notebook link from the same page
  object to the same target exists, by anyone, so a link the app asserted
  and wrote into the text is not doubled, and a later revision that keeps
  the reference proposes nothing (INV-5). Titles that merely match a Work
  are candidates the app offers, not claims (Settled 3).

**Judging it.** A notebook link is judged with
`archive.assessment/judgment` like any claim; disagreement stands side by
side (INV-7). When a later revision drops a reference the reader proposed,
the reader asserts `withdrawn` on its own link, which stays visible with
its history (INV-7) (Settled 7); a person's link stands until
they withdraw it.

### Wikidata joins catalogue correspondence (W2.10)

**`archive.claim/catalogue_correspondence` version 2** is version 1
(ADR 0087) with one more catalogue and one more subject role:

| `external` | Subject | `mapping` |
|---|---|---|
| `http://www.wikidata.org/entity/Q<n>` | `artist` | `skos:exactMatch` or `skos:closeMatch` |
| `http://www.wikidata.org/entity/Q<n>` | `work` (new role: a `musical_work` Entity revision) | `skos:exactMatch` or `skos:closeMatch` |
| `http://www.wikidata.org/entity/Q<n>` | `release` | `skos:broadMatch` |
| `http://www.wikidata.org/entity/Q<n>` | `recording` | `skos:closeMatch` |

- `<n>` is the item number in decimal with no leading zero. The URI is
  Wikidata's own concept URI, the one its RDF and query service use
  (`http`, not the `https` page URL), so World joins its cached items
  without rewriting, and two claims about one item compare as equal
  strings. Every other Wikidata form (a page URL, a property, a lexeme) is
  refused.
- A Wikidata album item gathers the editions of an album, as a MusicBrainz
  release group does, so a `release` (one edition) maps to it only by
  `broadMatch`. A Wikidata song item often mixes the composition and its
  best-known recording, so a `recording` maps to it only by `closeMatch`.
- The `work` role is new because Works keep ADR 0042's contract for
  MusicBrainz, which version 2 still refuses here; Wikidata is not a second
  way to say the same thing.
- Version 1 stays installed and valid; a release may install both.

Everything else is ADR 0042's and ADR 0087's: World may propose a
correspondence but never commits one; a candidate is promoted only when a
person asserts it, or when a saved response is admitted and pinned as the
claim's `document`; the claim stays operator-only through the relay
(ADR 0094). World's candidates come from its cache of Wikidata and from
MusicBrainz's own links to Wikidata items, so an Entity that already maps to
MusicBrainz is usually one confirmation away.

**Wikipedia is reached through Wikidata.** An article URL in a page is
text. World resolves it to its Wikidata item through the item's sitelinks,
which are outside knowledge and World's alone (INV-10). When a kept
correspondence maps that item to an archive Entity, the page view relates
the page to the Entity "through Wikipedia", computed from the page's text,
the correspondence claim and World's sitelink, and never stored. A person
who wants that relation kept without the sitelink asserts a notebook link,
which is the promotion. Items that correspond to nothing (a city, a
technique) stay outside links in the view.

### World and the relay

- **Projection.** A page revision is projected as any document Artifact
  revision, with its supersession chain and its source-path claim's
  notebook and path. A notebook link is projected as a claim shaped like a
  Web Annotation (ADR 0011): the page with its locator as target, the
  archive object (and `at`) as body. A Wikidata correspondence is projected
  as ADR 0087's are, between a local SKOS concept and the external one.
- **The page-link view (W2.10).** World parses each page's head revision
  for links to other pages: Obsidian `[[Page]]`, `[[Page|alias]]`,
  `[[Page#Heading]]`, embeds `![[…]]`, and Markdown links to relative `.md`
  paths; Org `[[file:…][…]]` and `[[id:…]]`. A wikilink resolves as
  Obsidian resolves it, by base name, preferring the shortest path; an
  ambiguous or missing target is shown as such. Syntax follows the page
  path's extension. The result lives in World's derived graph only,
  regenerable from the pages, and is never written back.
- **Views.** `notebook_page` answers one page: its head text's links to
  other pages, its backlinks, its notebook links with their standing, its
  outside links with any Wikidata item and corresponding Entity, and its
  revision history. `notebook_links` answers the reverse for an archive
  object: the pages that link to a recording, Work, session, collection or
  sample, which is what the review app, the songbook and the sample app
  show as "in the notebook".
- **Search.** Page text (head revisions only) is a text source of the
  retrieval index's full-text search ([ADR 0102](0102-retrieval-index.md))
  from the start, beside transcripts (R6.4). Results are filtered by the
  caller's readable set before they leave the relay (W2.7).
- **Relay.** Page bytes are served by `GET /documents/{digest}`, which
  already serves any Artifact a `document_source` describes, under
  `may_show`. A page reaches a member only through a publication approval
  to their audience ([ADR 0093](0093-audiences.md)), like any object; a
  notebook link pins its page, so it reaches no further than the page and
  its target. The relay gains only the notebook link under `annotate`.
  Snapshots never pass through the relay.

### New nouns

- **Notebook link** (a claim contract). (1) It says a written page is about
  an archive object, which no claim says: `document_source`'s `work`
  names only Works and describes a document's subject as a whole, and a
  performance-to-work link relates audio. (2) No verb; asserted and judged
  like any claim. (3) None; it lets World show what was written about
  what, and its pins keep INV-21 and INV-26 through reach. (4) It is code
  inside an existing concept: a claim contract checked in World and by
  Door's profile rules. (5) Its claims are evidence. (6) A contract, not a
  subsystem.
- **Page** (a notebook's file). (1) It names an Artifact that is a
  document with a source path in a notebook; nothing else is new. (2) No
  verb. (3) None. (4) It is an Artifact with existing claims. (5) Its
  revisions are evidence, as any Artifact's. (6) A term, not a noun; it
  exists only to avoid *note*, which means note events.
- **Notebook.** (1) It names a `collection` Entity whose members are
  pages. (2)-(6) As `collection` (ADR 0100); no new kind.
- **Page link** (the view of links between pages). (1) It answers which
  pages a page links to and from. (2) No verb; World computes it.
  (3) None. (4) It is a view in World. (5) No; regenerable. (6) A view.
- **Snapshotting** is an operation, not a noun: `capture notebook`
  admitting changed pages through Door's *admit*. *Snapshot* as a noun is
  only [ADR 0101](0101-daw-projects.md)'s project snapshot; the vocabulary
  reconciles the two uses when these records are accepted (R6.17).

## Consequences

- The operator's notebooks are kept with their history at the cadence they
  choose, and a quiet day costs nothing. Losing a notebook folder, or a
  sync service, loses at most what changed since the last snapshot.
- Applications show "in the notebook" beside a take, a Work or a sample,
  and the notebook app shows backlinks across both notebooks without the
  archive storing any page-to-page link.
- A Wikidata correspondence links an artist, Work or release to every
  language's Wikipedia article and to the many catalogues Wikidata links,
  one confirmation each.
- Pages are private by default: no member reads one until the operator
  approves it for an audience, and a label on a page covers its whole
  history.
- A first snapshot of a large vault admits two or three records per page;
  the command sends them in batches. Afterwards only changed pages cost
  anything.
- Pages are text and contain no audio; an attachment in a vault is not a
  page. Images and PDFs may later be snapshotted as ordinary documents with
  source paths; audio enters only through voice capture
  ([ADR 0103](0103-voice-capture.md)), never through the notebook command
  (Settled 8).
- No invariant changes. INV-5 holds because unchanged pages write nothing;
  INV-10 holds because sitelinks and parsed page links stay in World, and
  what is kept is promoted as a claim.

## Alternatives considered

- **ADR 0085's dated exports: a new Artifact per snapshot**, grouped by
  path. A path is not a stable key (pages move), there would be no object
  to label or link across revisions, and the decision says revisions.
- **A path in `document_source`.** Its `filename` is never a path by
  design, and W2.4 already makes the source path its own claim; one shape
  for samples and pages is simpler than two.
- **Page-to-page links as claims.** W2.10 makes them a view; claims would
  restate the text on every edit, against INV-5.
- **Archive references in the text as a view too.** W2.10 makes them
  claims, and a claim is what other applications and reach can rely on
  when the text later changes.
- **Snapshotting every save** (a file watcher writing on each change).
  Obsidian saves every few seconds; quiet history would still record each
  distinct state. W2.11 chose a cadence.
- **A Wikipedia article URL as `external`.** One item has an article per
  language, so one Entity would gather many equal mappings; the item is
  the concept.
- **A new Entity kind `notebook`.** A notebook is a set someone keeps,
  which `collection` already is, widened to any media (R6.1).
- **Membership by source paths' `within`.** It would make a second way to be
  a member beside membership claims, and a removal would need a field the
  source-path claim does not have; membership claims already record
  additions and removals as revisions (R6.1).

## Touch points

- `world/collections.py` (new, ADR 0100): `members`, read for a notebook's
  pages.
- `capture/notebook.py` (new): the `capture notebook` command (membership
  revisions for new and removed pages), using
  `enrich/adapter.py` to read the archive and stage content as
  `capture/capture.py` does; `capture/README.md`.
- `world/notebook.py` (new): `check_notebook_link`, the page-link parser
  for Obsidian Markdown and Org, the archive-reference reader, and the two
  views' builders.
- `world/documents.py`: `document_lines`, reused for locators;
  `check_document_source`, unchanged; `_pins` and `Reacher`, unchanged (a
  notebook link's pins already bound its reach).
- `world/catalogue.py`: `external_entry`, `SUBJECT_KINDS` and
  `check_correspondence` gain the Wikidata entry and the `work` role
  under a `CORRESPONDENCE_2` contract; `subject_of` and `corresponding`
  read both versions.
- `world/circle.py`: `MEMBER_CONTRACTS` and `check_member_draft` gain the
  notebook link under `annotate`.
- `world/views.py` (`VIEWS`) and `world/record_views.py`: `notebook_page`
  and `notebook_links`.
- `world/contract_graphs.py` and `world/projection/6/vocabulary.ttl`: the
  notebook link graph, page locators, and the Wikidata concept scheme.
- `relay/access.py`: `check_drafts`, unchanged in shape, reading the new
  member contract.
- `door/src/Archive/Door/PinnedRules.hs` and `RecordRules.hs`: expected
  unchanged; the bundles' profile rules carry the role counts and kinds.
- `spec/archive-spec.md`: a new D-section for the notebook link and
  catalogue correspondence version 2 (beside D-18's version 1);
  `docs/vocabulary.md`: *notebook link*, *page*, *notebook*, *page link*,
  and *snapshot* reconciled with ADR 0101 (R6.17).
- `docs/decisions/0007-policy.md`: the snapshot desire, amended on
  acceptance (R6.14).

## Settled by the operator (2026-10-19)

1. **Policy gains a snapshot desire.** ADR 0007 is to be amended on
   acceptance with a snapshot desire: cadence a standing desire, last-run
   time operational state (R6.14).
2. **Recording a removal.** Recorded, never a retire (R6.16), by revising
   the notebook's membership claim without the page, since membership is
   always by membership claims (R6.1); no source-path field is needed.
3. **Machine links from titles.** Only explicit archive references are
   proposed; title matches stay candidates in the app (R6.16).
4. **Members' notebooks.** Snapshots stay the operator's in wave 2; a
   friend with `admit` deposits Markdown as ordinary documents (R6.16).
5. **Origin of pages.** None for now; decided when publishing a page beyond
   the circle is first wanted (R6.16).
6. **A document kind for pages.** `kinds` absent; the notebook says what the
   document is (R6.16).
7. **A dropped reference.** The reader withdraws its own link; it stays
   visible with its history (R6.16).
8. **Vault attachments.** Images and PDFs later as ordinary documents with
   source paths; audio only through voice capture (R6.16).
9. **A notebook is a collection.** `collection` is widened to any media and
   the snapshot command proposes its membership claims (R6.1).
10. **Page text in the index.** Now, beside transcripts (R6.4).
11. **The word "snapshot".** Snapshotting here is an operation; the noun is
    ADR 0101's; reconciled in the vocabulary on acceptance (R6.17).
