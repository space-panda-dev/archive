# Open questions

These are left open on purpose. A gap listed here is a known gap, not an
oversight.

Do not answer one just because an answer can be produced. Resolving one takes a
decision record (or a specification change) that a person accepts, and then an
update here. Code that depends on an open answer should fail loudly as
unsupported rather than guess.

Known gaps that need work rather than a decision (measured weaknesses,
follow-ups, clean-ups) are in the [backlog](backlog.md).

## The archive contract

- Specification 0.0.5 installs the V2 migration route contracts after the
  World releases ([ADR 0046](decisions/0046-world-release-before-v2-migration.md)).
  Which release carries the ADR 0036 bundle amendment in its text? (The run
  record a `work_match` claim needs is settled by
  [ADR 0064](decisions/0064-derivation-runs.md) and
  [ADR 0067](decisions/0067-matches-and-alignments-pin-runs.md).)
- The selection facts of 0.0.5 pin only version 2 selections. Does a
  single-track version 1 selection ever need a title, kind, or credit, or is
  it superseded by a version 2 revision first?
- The `audio_stream` role `artifact`, the `span_suggestions` role `source`,
  and the `derivation_run` payload field `software`, named by
  [ADR 0065](decisions/0065-audio-stream-facts.md),
  [ADR 0066](decisions/0066-span-suggestions.md), and
  [ADR 0064](decisions/0064-derivation-runs.md), are core terms that a
  contract fragment may not redefine, so specification 0.0.7 interprets them
  as `arch:Artifact`, `arch:source`, and `prov:SoftwareAgent`. D-6 also asks
  each fragment to define its own payload and role terms, which these three
  cannot do (the `capture_alignment` role `reference` of 0.0.5 is the earlier
  case). Should a later contract version rename them? Until one does, World
  projection 6 (revision 3) keeps an `artifact` or `source` reference only as
  its reified statement, with no direct triple by `arch:Artifact` or
  `arch:source`, and reaches the same targets through predicates of its own
  (`arch:audio_stream`, `prov:wasDerivedFrom`); `software` items become
  `prov:SoftwareAgent` nodes. Whether that is the lasting projection is part
  of this question.
- The role `run` maps to `prov:wasGeneratedBy` and pins the run's Claim
  revision, so that revision is the activity
  ([ADR 0064](decisions/0064-derivation-runs.md)). World types every record
  revision `prov:Entity` in its records graph, and a run's revision
  `prov:Activity` in the run's own graph, but PROV holds entities and
  activities disjoint. Should a run's revision and its activity be two
  nodes, and would that need a contract change or only a new projection?
- Should Door refuse new `work_match` version 1 claims, which pin no run, now
  that version 2 exists ([ADR 0067](decisions/0067-matches-and-alignments-pin-runs.md))?
  Version 1 stays installed for the records that cite it.
- A `session_parts` claim does not say which recorder source it lists (the
  Zoom H4essential's `TrLR` or `TrMic`); a client recomputes it from the
  claim's name. Should a later contract version record the source, and the
  primary one, in the claim
  ([ADR 0070](decisions/0070-capture-import.md))?
- How does an existing archive migrate to a specification version that is not
  additive? (Additive releases are adopted by a transition,
  [ADR 0068](decisions/0068-specification-upgrade.md).)
- For V2 migration ([ADR 0035](decisions/0035-v2-migration-preserves-evidence-and-claims.md)),
  accepted ADR 0036 specifies one deterministic PAX/USTAR bundle, an RFC 8785
  manifest, a whole-container content digest, a fixed identity for the first
  frozen V2 import, and member locators paired with a pinned bundle reference.
  A later independent V2 source needs a separately accepted identity and
  accounting rule; records added after the frozen bundle cannot replace it.
- Which exact contracts and provenance fields define the manifest, core
  content, incident, and recovery claims? What source relationship establishes
  that repeated retries belong to one incident, and what positive evidence is
  enough to establish recovery? What stable semantic identity allocates each
  translated claim, and how are each source schema's `by` and `agent` values
  mapped? ADR 0036 accepts the bundle mechanics and scope; these claim routes
  remain open. Until contracts and identity rules are accepted, migration may
  report candidates but must not propose those claims.
- Which exact Archive contracts and identity/reference rules support claims
  from each populated V2 schema? Each claim must be complete within its
  accepted contract's defined scope; unprojected source fields remain visible
  in the migration report. A V2 contract or matching field type does not settle
  a route.
- For the required V2 reviewed-section-to-song route, the assertion links to
  a distinct song entity; title and cover-song distance may support a
  machine-asserted link but cannot be that entity's identity. The review app
  owns the first-identification workflow and proposes the stable identity to
  Door. Accepted ADR 0040 settles the candidate identity allocation, the
  selection, assertion, and match claims, their references and provenance, and
  exact sample-boundary rules. Its contract draft remains proposed: the
  canonical JSON Schemas and contexts must be written to the source rules in
  [ADR 0047](decisions/0047-v2-audio-route-source-rules.md) and released
  before migration can propose these claims. How should a performance that
  spans more than one segment chain be translated (ADR 0047 leaves it
  unresolved)? How will the resulting
  claims be queried and checked against the V2 source locators? Per-kind
  currentness remains open.
- What query surface consumes admitted claims, and which parts use Archive
  claims directly versus a World projection? Per-kind currentness remains open.
- How does content identity migrate when a digest algorithm weakens? RFC 6920
  identifiers carry their algorithm ([ADR 0012](decisions/0012-inherited-meaning.md));
  the re-hashing procedure is open.
- The exact PREMIS Level 1 mapping table, and the Spectrum mapping for physical
  procedures ([ADR 0012](decisions/0012-inherited-meaning.md),
  [ADR 0013](decisions/0013-physical-collections.md)).
- How exactly do MusicBrainz kinds map onto the Library Reference Model (for
  example, whether a MusicBrainz Recording is an LRM Expression)? This now
  covers the `release` and `recording` Entity kinds of
  [ADR 0078](decisions/0078-contributions.md), which take MusicBrainz's
  meaning and no LRM class yet.
- What origin values mark the operator's own work across media (a recording, a
  book, a photograph) ([ADR 0010](decisions/0010-publication.md))? A musical
  Work's origin, co-written Works included, is [ADR 0088](decisions/0088-bandmates.md)'s `work_origin`;
  recordings and other media have none. ([ADR 0096](decisions/0096-origin-of-recordings.md), proposed, would answer
  this for recordings and Artifacts.)
- Does a disclosure to the circle need a policy revision before the policy
  language exists? [ADR 0088](decisions/0088-bandmates.md) records it as a publication approval, which S-32
  ties to a policy revision.
- How are jurisdiction-specific rights terms computed, and from what facts?
- Does the collection need archival arrangement (collection, series, item, as in
  DACS)? Not at single-operator scale yet.

## Door

- Can git-annex keep INV-5? Run the operations apply will use twice, on a copy of
  a real archive, and compare the git-annex branch. If an unchanged rerun
  writes, the invariant or the mechanism changes. A synthetic local probe on
  git-annex 10.20260901 found that repeated `metadata --set want=hot`, `wanted`,
  and `group` commands each advance that branch even with unchanged arguments.
  `whereis --json` and `info --json` did not. A further synthetic probe with a
  local directory special remote found that immediate identical `copy --to`
  and `drop` retries left the branch unchanged, while each repeated `fsck` and
  `fsck --from` advanced it by changing `activity.log`. Apply must account for
  those writes rather than assume verification is read-only. For content
  admission, the operator accepted a 2026-10-14 run on real content as meeting
  the real-copy gate: every admission step was quiet on an unchanged rerun
  except git-annex `fsck`, so Door verifies content by hashing it directly.
  Whether merging a staging remote's location logs on first contact is
  meaningful history remains open, as do SSH transfer, a managed repository
  target, and the operations apply will use.
  The repeatable synthetic harness and both runs are in the
  [git-annex INV-5 experiment](proposals/git-annex-inv5-experiment-review.md).
- Does the seedbox's torrent client tolerate read-only symlinked files across
  force-recheck, seeding, and restart? A synthetic, disposable test is needed;
  it answers nothing until evidence from an exact client/version/filesystem
  tuple exists.
- Beyond retire, destroy, publication, and copy reductions, which transitions
  are consequential enough to need provenance records? ADR 0028 defines
  dispatch only for the already settled bootstrap, retire, and destroy kinds;
  this question remains open.
- How is expunging (erasing a record and its Git history) designed, when it is
  needed? Until then it is manual.
- What does **information erasure** mean for evidence derived from destroyed
  content (verbatim transcripts, thumbnails, fingerprints, embeddings)? Storage
  deletion and privacy deletion differ: keeping a transcript after deleting a
  WAV suits the first and may defeat the second. Destroy does not answer this.
- What form does a recorded reclaim verdict take, and how is the verification
  state it snapshots represented?
- How does an operator declare, ahead of time, what happens to an archive
  after them: a successor, content destroyed or sealed, content that opens after
  a closure period? Time-limited grants would be new to policy.
- How do separate archives relate (federation)? INV-3 makes concurrent
  operators separate archives, and nothing yet lets one archive pin, cite, or
  read another's objects: a friend's own sample library, say, kept in their
  own archive. Until this is decided, someone else's content enters only by
  the operator holding it in custody on their behalf, with the person
  recorded on its origin claim (as depositor, or as `captured_by` when they
  recorded it) and, where the content records them, their own limits
  binding ([ADR 0018](decisions/0018-people-in-the-archive.md)).
  ([ADR 0093](decisions/0093-audiences.md), proposed, keeps this open.)
- Door refuses a proposal written under a specification context the archive
  does not hold, and refuses a committed record written under a context only
  a later commit added. A committed record under a context the archive never
  held still loads, because archives written before the proposal check may
  hold one. Should loading refuse it too? A census of the shadow comes first.
- How does Door check the two rules specification 0.0.9 states but does not
  enforce? The operator decided (2026-10-18) that a later release adds both
  checks; until then Door admits a claim that breaks either, and only the
  asserter stands behind it.
  - A `contribution` that a chord stacks claim pins must be about the stack's
    recording ([ADR 0078](decisions/0078-contributions.md)). Door checks only
    that it is a contribution version 1 Claim. A check must compare the
    pinned claim's subject (its `recording`, or the `source` of a span or
    channel claim) with the stack's `source`; the target rules read a pinned
    claim's payload but never compare its references with the record's own.
    It must also say what counts as the same recording when the
    contribution names a `recording` Entity rather than the Artifact.
  - An `analysed` Artifact, or a stem cited by bank key ([ADR 0079](decisions/0079-machine-harmony-claims.md)), must share
    the mix's timeline: its sample n is the source's sample n
    ([ADR 0077](decisions/0077-chord-stacks-and-meter.md)).
    This is a fact about audio, not about records, so Door has nothing to
    compare until some claim measures it (an alignment between the stem and
    the mix, say); which claim, and what tolerance, is undecided.
- Specification 0.0.10 (D-18) makes Door compare a pinned tuning's or
  notes claim's `source` with the record's own
  ([ADR 0086](decisions/0086-notes-and-melody.md)), and leaves to World the
  cross-claim rules that read a pinned claim's payload: a section or entry
  index inside the pinned form or sequence, a placed bar's time signature
  ([ADR 0083](decisions/0083-form-and-work-meter.md)), a tune that fits its
  meter, and a melody span inside its notes claim's range. Which of these,
  and the contribution a notes claim pins, should a later release make
  Door's?
- How is operator succession verified? (Author and authorizer are agents in
  PROV roles: [ADR 0012](decisions/0012-inherited-meaning.md).) ADR 0026 defines
  only initial bootstrap; this succession question remains open.

## Claims and provenance

- Should evidence that is original or asserted and evidence that is derived and
  kept remain two named classes, or become one class told apart by provenance?
  ([state-model.md](state-model.md) currently treats them as one class that
  arrives two ways.)
- How is a person's Entity kept stable across name changes and successor
  operators? (Agent identity: [ADR 0012](decisions/0012-inherited-meaning.md).)
- What constitutes a claim's identity across kinds?
- How are annotations, assessments, and enrichment results represented and
  versioned in their contracts? ADR 0027 defines the common contract boundary;
  ADRs 0044 and 0045 settle audio selection and song occurrence contracts,
  and [ADR 0081](decisions/0081-judgments-and-annotations.md) settles scoped judgments and span annotations. Review outcomes
  and other domain contracts remain open.
- Does predicate-specific evidentiary authority need explicit modelling at all?
  (See [provenance.md](provenance.md).)
- What are the per-kind rules for computing "current"? [ADR 0082](decisions/0082-views-and-current.md) settles the
  first, for chords on a recording; every other kind still has none.
- Should the work matcher's corpus honour judgments? Today every head
  `performance_work` link counts, including one a person judged `rejected` or
  `withdrawn` ([ADR 0067](decisions/0067-matches-and-alignments-pin-runs.md)
  names no rule).
- How does the archive record whether corpus audio is the operator's own
  recording or a commercial release? The V2 system suggested release matches
  at a looser distance (about 0.11, on the exact scale); until an origin is
  recorded, every corpus entry is judged alike, at 0.118 on the quarter-rate
  scale and 0.08 on the exact one
  ([ADR 0071](decisions/0071-essentia-cover-similarity.md)). ([ADR 0096](decisions/0096-origin-of-recordings.md),
  proposed, would answer this.)
- Do the window matcher's thresholds hold outside the recordings that chose
  them? The hit limit (0.20), the quarter-rate suggestion distance (0.118),
  and the near-tie margin (0.02) were chosen on one calibration workset of
  98 accepted occurrences, and leave-one-recording-out did not predict the
  wrong suggestions that followed. They need a held-out set of recordings, and
  a listening pass over the unaccepted suggestions, before their precision is a
  claim ([ADR 0072](decisions/0072-windowed-work-matches.md), amendment of
  2026-10-18).
- Do quarter-rate distances agree across BLAS libraries closely enough that a
  run on another machine suggests the same windows? Runs record the library,
  its kernel, and its one thread. On the calibration workset, Accelerate on an
  Apple M2 and OpenBLAS on a Haswell CPU made the same decisions, with all but
  one of 22,177 quarter-rate distances bit-equal; other libraries and kernels
  are unmeasured
  ([ADR 0071](decisions/0071-essentia-cover-similarity.md)).
- Should a window match name more than its closest Work? Each suggested window
  claims one Work; the other Works within the distance are kept in the run's
  regenerable answer, not claimed, and none of them has been judged
  ([ADR 0072](decisions/0072-windowed-work-matches.md)).
- Should the whole-recording search record more than its claims: hits,
  alignment paths, or each corpus occurrence's presence along the recording?
  Hits are computed and discarded; paths and presence are not computed
  ([ADR 0072](decisions/0072-windowed-work-matches.md)).
- How does the window matcher stay cheap as the corpus grows? Every corpus
  occurrence passes along every recording, and an accepted occurrence makes the
  next run search each recording again
  ([ADR 0072](decisions/0072-windowed-work-matches.md)).

- What are the current rules for meter layers, a Work's chord sequences, keys,
  form, and melody ([ADR 0077](decisions/0077-chord-stacks-and-meter.md))?
  [ADR 0082](decisions/0082-views-and-current.md) settles one for chords on a recording, as a named view that takes a
  person; each other kind needs its own named rule, and asking for it stays
  unsupported until one is accepted.
- Should another person's correction ever apply in someone's current view?
  [ADR 0082](decisions/0082-views-and-current.md) lists a bandmate's correction beside the choice and applies it only
  in the bandmate's own view.
- How are two asserters' beat grids matched? Two meter claims over the same
  recording place the same beat a few milliseconds apart, and a chart's bar 17
  may be a machine's bar 18. Comparing them needs a tolerance and a
  correspondence between bars that no contract states; until then World shows
  both and pairs nothing.
- Are `exactly_one` and `maybe` enough to express uncertain tones, or does a
  reading need ranked alternatives within a group, or uncertainty about the
  bass?
- Pitch classes name steps of a twelve-tone grid, which a tuning claim places
  on a recording ([ADR 0080](decisions/0080-key-and-tuning.md)). How is a microtonal passage, whose notes fall
  between the grid's steps, kept? Notes hold offsets of up to half a step in
  cents ([ADR 0086](decisions/0086-notes-and-melody.md)), which is exact but not how such music is written. Should
  JAMS or MusicXML exports carry the tuning?
- Are a label, a release event, a barcode, or a catalogue number catalogue
  entries or claims of their own? [ADR 0087](decisions/0087-tracklists-and-correspondence.md) leaves them out. And how does Review
  surface two Entities that both map `exactMatch` to one outside entry?
- What Entity kind credits a place, such as a studio? MusicBrainz credits
  places, but local place identity is open
  ([ADR 0043](decisions/0043-geographic-evidence-and-route-handoff.md)), so a
  place credit keeps the place as text and pins no contributor.
- Which edition does a MusicBrainz release-group credit mean, and is a
  release group an Entity kind? Release-group credits are not imported; a
  release only maps to a release group or a Discogs master as broader
  ([ADR 0087](decisions/0087-tracklists-and-correspondence.md)).
- Should the resolved view of credits relate a span on a mix to a channel of
  the same take? A channel and the mix are different Artifacts; only a
  capture alignment relates their samples, and the view does not use one.
- Should a contribution say more than MusicBrainz can, such as a part within
  an instrument (second trombone) or a doubling? Today that survives only in
  the credit text.
- How does a person's sensitivity limit "the rehearsals I play in"
  ([ADR 0018](decisions/0018-people-in-the-archive.md)) relate to
  contributions? Channel and span credits could select that content; whether
  they may, and who asserts them, is undecided.

- How does a person's sensitivity claim select the content it covers ("the
  rehearsals I play in") rather than naming objects one by one
  ([ADR 0018](decisions/0018-people-in-the-archive.md))?
- May a bandmate with a relay login assert their own sensitivity limit, at
  least one that only narrows what is shown? [ADR 0088](decisions/0088-bandmates.md) keeps sensitivity claims
  the operator's to send, so the operator records a bandmate's limit with
  them as its author. ([ADR 0094](decisions/0094-peoples-writes.md), proposed, would answer this.)
- Is [ADR 0085](decisions/0085-documents-lyrics-rights.md)'s rights permit table right: `InC` held by the operator permits
  distribution, `NoC-US` distribution, `NoC-NC` open, everything else (and no
  rights claim) service, and the strictest claim wins whoever asserted it?
  It governs only sharing beyond the circle: the operator decided the
  circle is private, so no rights statement limits it.
- Can a person opt in to signing their limits with their own key, so the
  operator can record a limit but not forge its withdrawal? Spool grants
  already hold the SSH public key of each friend who shares compute, and
  OpenSSH can sign with it (`ssh-keygen -Y sign`).

- What records where an admitted stem or practice render came from: its
  mix, separator, and settings? [ADR 0079](decisions/0079-machine-harmony-claims.md) admits a chosen stem as an Artifact
  whose digest its bank citations name, and [ADR 0089](decisions/0089-practice-audio-and-exports.md) a chosen render the same
  way, but no claim states the link. ([ADR 0098](decisions/0098-content-lineage.md), proposed, would answer this.)
- How are parts of untimed claims judged or marked: a Work's untimed meter or
  form, a Work-level melody, a key over sections ([ADR 0081](decisions/0081-judgments-and-annotations.md))? They can be judged
  only whole, and a mark on a Work needs a chord sequence to range over.
- Should sections nest (a verse of two phrases), and should a section's
  `kind` map to an inherited vocabulary such as SALAMI's function labels
  ([ADR 0083](decisions/0083-form-and-work-meter.md))? How is a recording's departure from the written form (an extra
  chorus, a cut bridge) matched rather than only shown?
- How is a Work's melody placed on a recording note by note, how do lyric
  syllables sit under melody notes, how are a tune's verse variants under
  repeats and its grace notes written, and how does World join one asserter's
  split notes claims of a part and check that their ranges do not overlap
  ([ADR 0086](decisions/0086-notes-and-melody.md))?
- For the amendments of 2026-10-19 to
  [ADR 0086](decisions/0086-notes-and-melody.md) and
  [ADR 0085](decisions/0085-documents-lyrics-rights.md): when is staccato too
  dense to read (43% of one track's notes), and should the written rhythm
  view then say so rather than mark every note? Are its initial thresholds
  (under 60% sounded for staccato, at least a beat for a rest) right? Where
  does a view put the half of the syllables that no note carries? What
  reference timings measure forced alignment well enough to state its
  `max_error_samples`? How is the operator's override past a sensitivity
  limit (one destination, no further than service) recorded, so that Door's
  check beyond the circle can honour it?
- Can a lead sheet part say a partial or per-string capo? Is a band's
  adoption of a lead sheet checked against the members' membership dates,
  and does adoption need a quorum ([ADR 0084](decisions/0084-lead-sheets.md))?

## Policy

- What is the exact grammar of the Datalog subset
  ([ADR 0007](decisions/0007-policy.md)), and what
  conformance fixtures ship with the spec? A clingo prototype against a copy of
  real records informs both.
- Does a friendlier surface syntax earn its keep, and how does it compile down?
- What freshness window makes a verification recent enough for an unattended
  drop or an observed copy to count? (Routine verification is local operational
  state: [ADR 0008](decisions/0008-plan-and-apply.md).)
- How is failure-domain membership configured and checked?
- What accepted configuration format and SSH capability protocol bind a
  private managed location? [Proposed ADRs 0037](decisions/0037-proposed-private-managed-copy.md)
  and [0038](decisions/0038-proposed-private-placement-protocol.md) draft a
  concrete whole-Archive copy and control protocol without closing this
  question or authorizing remote setup.
- How is a rule's stable name written, and how are matched facts recorded in a
  transition?
- Where do derivation constraints (contract version, minimum tool version) and
  per-kind re-derivation rules live in the syntax?
- How is human authorship of a policy revision established and recorded?
- Where do machine proposals queue before a person admits one?
- What fields does a disclosure claim carry
  ([ADR 0016](decisions/0016-disclosure.md))?
- What period and granularity do usage aggregates use
  ([ADR 0011](decisions/0011-world.md))?

## World

- Which vocabularies beyond the first musical Work and MusicBrainz
  correspondence does World need? ADRs 0041 and 0042 settle only the first
  slice.
- How is evidence for a specific external response admitted and pinned before
  a World candidate based on that response is promoted? Manual assertions do
  not require an external response (ADR 0042).
- [ADR 0043](decisions/0043-geographic-evidence-and-route-handoff.md) settles the boundary for saved observations, temporary route preferences, and per-route navigation handoff. Local place identity and listing correspondence, observation granularity and contracts, actual-track evidence, routing-provider inputs, lasting preference rules, and Apple Maps handoff fidelity remain undecided. This does not extend World v1's accepted projection.

## Interfaces and tooling

- Are `plan` and `apply` top-level commands or `archive` subcommands? Is
  `archive views` the right name? (Sketch:
  [boundaries.md](boundaries.md#commands-provisional).)
- How does Archive's adapter to Spool work? Spool's protocol is settled in its
  own repository; open is Archive's side: which enrichments become which
  capabilities, how content is staged as attachments (Spool names them by
  SHA-256, as Archive names content), how the disclosure is recorded before the
  put, and how a result becomes a Door proposal exactly once.
- When Archive places a copy on a machine outside the operator's custody (a
  friend's, to avoid resending content for every task), how does that combine
  placement (ADR 0009) with disclosure (ADR 0016): one recorded disclosure per
  copy, and does it count toward failure domains?
- The operator chose plaintext seedbox placement with authentication on every
  served route ([proposed ADR 0039](decisions/0039-proposed-hosted-plaintext-placement.md)).
  What exact policy syntax, disclosure claim fields, and viewer credential
  management make each full-copy item and any projection authorized before
  transfer or publication?
- Where does program configuration live, and in what format?
- The feature bank and the extractor contract
  ([ADR 0073](decisions/0073-feature-bank.md),
  [ADR 0074](decisions/0074-extractor-contract.md)) leave
  these open:
  - Should waveform peaks and listening copies become extractors in the same
    per-content directory, and how would the relay then name them
    ([ADR 0069](decisions/0069-derived-media.md))? Or should a request be
    able to name derived media as an input, so that an extractor on their
    grid (the `colour` extractor follows the peaks pixel by pixel) reads them
    instead of decoding the content again?
  - A key holds no software versions, so a bank filled on another machine with
    another `ffmpeg` or another build of a library may hold other bits under
    the same key. Should a reader refuse entries whose recorded software
    differs from its own, or should a key include a fingerprint of it? (A
    claim made from features keeps the software either way: its run copies
    each read entry's `software`.)
  - How is the disclosure of content to a worker outside the operator's
    custody recorded before the put (the Spool adapter question above)?
    [ADR 0090](decisions/0090-extractor-runs-travel-through-spool.md)
    returns an extractor's files as Spool result attachments and runs only
    on the operator's own workers; it does not answer this.
  - A grid is a time base in integer samples, which covers video's
    fractional frame rates. How are spatial grids and regions of an image
    declared, since spans are one-dimensional?
  - Should an entry record its stream's start offset in the container, so
    that a reader relating a video's frames to its audio features need not
    probe the file again?
  - Destroying content (ADR 0004) must delete its directory in the bank and
    in the derived-media root (ADR 0069), stems and practice renders
    included. Is that a step of the destroy transition, a pruning command run
    after it, or a check in every reader against World's destroyed digests?
    Exports ([ADR 0089](decisions/0089-practice-audio-and-exports.md)) are filed by export key, not under a content's
    directory: how does destroying content find the exports that render
    revisions citing it?
  - Practice audio ([ADR 0089](decisions/0089-practice-audio-and-exports.md)): should the import chain separate every
    recording, is a lossy practice copy needed for phones, should the relay
    cut excerpts, and does the client's live mix or a server render serve
    better (D11.5 leaves that to testing)?
  - Should decoded audio at another rate (44.1 kHz for a separator) be a
    shared feature, so that extractors off the `frames` grid stop decoding
    again, at about 1.3 GB per four mono hours at 22,050 Hz?
  - When should a `frames` version keep each frame's spectral peaks, or a
    summary of them (a deviation histogram, dissonance, a profile fine enough
    to refer to any tuning), so that `tonal` stops repeating `frames`' peak
    picking, twice? ADR 0074 says: when a second extractor needs them.
  - An entry belongs to one content. How is a feature of several contents
    (two recordings' cross-similarity) filed and keyed?
  - A stem is measured as content of its own
    ([ADR 0075](decisions/0075-stems-as-content.md)), so
    an extractor run on it cannot also need a feature of the recording it
    came from (a vocal line's level against the mix's `frames`). Should
    `needs` be able to name the source content's features, and how would the
    key hold them?
  - Stems are separated in 600-second chunks joined by crossfades. How far
    does a long recording's chunked separation differ from a whole-file run
    on real material, and does it matter to any measure?
  - Whether a stem holds what its name says depends on who played: an empty
    `bass` is right where there is no bassist. Should a lineup be a claim a
    stem's measures can be read against, and where would it come from?
  - What deletes stale entries, whose keys no installed extractor gives: a
    command, a policy, or only the operator deleting the root?
  - Which field of a derivation run's parameters records the features a
    claim-producing tool read? ADR 0073 proposes each feature's name, its
    extractor's id and version, and its entry key; the first such tool
    settles the shape.
  - May the window search read windows' pitch-class profiles from the
    whole-recording `frames/hpcp` series? Their edge frames differ from a
    window measured alone, so it is another method needing its own
    calibration ([ADR 0071](decisions/0071-essentia-cover-similarity.md)).
- Where does `enrich.mk`-style mechanism end and derivation policy begin, once
  the policy language exists?
- When `capture import` stops part way through its enrichment chain, a rerun
  skips the sessions it committed, because their contents are now Artifacts,
  and answers `unchanged`. Should the importer report or finish the chain for
  sessions it recognises as its own? ADR 0070 names only new Artifacts; until
  a decision, the chain is finished by hand
  ([shadow runbook](shadow-runbook.md#importing-new-recordings)).
- At cutover, which archive becomes the real one: the shadow, promoted, or a
  fresh migration of the same frozen bundle? See the
  [cutover plan](proposals/cutover-plan.md).

## The project

- Who governs Archive, the project, once there are other regular contributors?
  Decide it when they arrive, with them.
