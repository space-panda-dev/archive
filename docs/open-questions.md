# Open questions

These are left open on purpose. A gap listed here is a known gap, not an
oversight.

Do not answer one just because an answer can be produced. Resolving one takes a
decision record (or a specification change) that a person accepts, and then an
update here. Code that depends on an open answer should fail loudly as
unsupported rather than guess.

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
  example, whether a MusicBrainz Recording is an LRM Expression)?
- What origin values mark the operator's own work across media (a recording, a
  book, a photograph), and how are co-authored works (a band's album) handled
  ([ADR 0010](decisions/0010-publication.md))?
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
  ADRs 0044 and 0045 settle audio selection and song occurrence contracts.
  Review outcomes and other domain contracts remain open.
- Does predicate-specific evidentiary authority need explicit modelling at all?
  (See [provenance.md](provenance.md).)
- What are the per-kind rules for computing "current"?

- How does a person's sensitivity claim select the content it covers ("the
  rehearsals I play in") rather than naming objects one by one
  ([ADR 0018](decisions/0018-people-in-the-archive.md))?
- Can a person opt in to signing their limits with their own key, so the
  operator can record a limit but not forge its withdrawal? Spool grants
  already hold the SSH public key of each friend who shares compute, and
  OpenSSH can sign with it (`ssh-keygen -Y sign`).

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
- Where does `enrich.mk`-style mechanism end and derivation policy begin, once
  the policy language exists?

## The project

- Who governs Archive, the project, once there are other regular contributors?
  Decide it when they arrive, with them.
