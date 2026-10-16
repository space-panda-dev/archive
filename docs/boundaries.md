# Boundaries

How the parts depend on each other, what is left out on purpose, and where new
code goes. The terms are defined in [vocabulary.md](vocabulary.md).

## Who depends on whom

The boxes are concepts, not programs or machines. Nothing here decides how many
programs exist or where they run.

```
          Policy (declares; changes nothing itself)
             │ read
             ▼
          plan ── evaluates policy against evidence,
             │     lists actions with their reasons
             ▼
          apply ── acts through git-annex and enrichment tools,
             │     proposes archival changes
             ▼
 Annotation ─┐
 Review ─────┤
 Enrichment ─┼── proposes ──►  Door  ── commits ──►  archive (evidence)
 World ──────┘                                          │
     ▲                                                  │
     └───────────── read / project evidence ◄───────────┘

 Spool: outside; any of the above may use it, and it depends on none of them
```

What the picture means:

- **Door** understands the invariants; that is its job. It depends on none of
  the parts that propose changes. It checks policy revisions but never evaluates
  policy.
- **Enrichment**, **Annotation**, and **Review** change the archive only by
  proposing to Door.
- **Policy** declares; changing policy is itself a Door transition.
- **Plan** reads only the archive's evidence and policy. Facts from World are
  promoted into the archive first.
- **World** reads and projects evidence. Its only way to make something durable
  is a claim admitted through Door.
- **Spool** may be used by anything here and depends on none of it. Work done
  through Spool counts for nothing until its result is proposed to Door.
- **Tools outside this repository** (Spool, agent tooling) reach archive
  content only through an adapter from here, so disclosure is checked
  ([ADR 0016](decisions/0016-disclosure.md)).

These rules are documentation, not interfaces. Build an interface to enforce one
only when real code needs it. A dependency cycle created for convenience, such as
Door calling back into World, is a design error.

The decisions behind these boundaries: Archive stands alone
([ADR 0001](decisions/0001-archive-preserves-evidence.md)); one Door
([ADR 0002](decisions/0002-one-door.md)); Spool and languages
([ADR 0014](decisions/0014-boundaries-and-languages.md)).

## Commands (provisional)

```
door admit | transition          the only commands that change archival meaning
door transition (upgrade)        adopting a later, additive specification release
door serve --socket <path>       the same commands for applications, on a private socket
relay/relay.py --config <file>   Door's service and World's query over HTTP, for the tailnet
door verify                      full verification of HEAD and a fresh checkpoint
archive init | status | doctor   setup, state, whole-archive check
archive maintenance repack       coordinated packing of loose Git objects
archive maintenance prune-packed  coordinated Git loose-object cleanup
archive export [--since <commit>] the committed snapshot, or what was added after a commit
archive ls | show | current      reading
archive plan | apply             reconciling (may become top-level plan / apply)
archive views                    rebuilding browsable views (name unsettled)
enrich/enrich.py probe           count an Artifact's audio streams and propose the facts to Door
```

`door serve --socket <path>` runs Door for one archive (the working
directory) on a Unix socket with owner-only permissions, in a directory closed
to other users ([ADR 0054](decisions/0054-door-service-for-applications.md)).
Each connection sends one line, `{"command": "admit" | "admit-dry-run" |
"transition" | "transition-dry-run", "proposal": <directory>}`, and receives
one line: `{"ok": true, "result": ...}` with exactly the JSON the command
prints, or `{"ok": false, "error": {"code": ..., "message": ...}}` with a
stable code ([ADR 0058](decisions/0058-door-refusal-codes.md)).
[`door/service.schema.json`](../door/service.schema.json) describes both lines. The service keeps the planning index
of the current HEAD in memory, takes the archive lock for every write like the
command line, and reloads when HEAD moved.

The [relay](../relay/README.md) carries Door's service and World's query to
the operator's browser over the tailnet
([ADR 0056](decisions/0056-tailnet-relay.md)). It turns a JSON proposal into a
proposal directory for the service, refuses content and any draft not naming
the caller's agent, and answers read-only SPARQL. It writes nothing itself.
[`relay/openapi.json`](../relay/openapi.json) describes every route, answer,
and refusal code ([ADR 0060](decisions/0060-relay-interface-and-role.md)).

[`enrich/`](../enrich/README.md) holds enrichment
([ADR 0064](decisions/0064-derivation-runs.md)): tools that take files and
return plain results, and one adapter that reads the archive through
`archive export`, packages a result as a derivation run with its outputs, and
proposes them in one `door admit` batch. The first tool is the audio stream
probe ([ADR 0065](decisions/0065-audio-stream-facts.md)), which needs `ffmpeg`
and `ffprobe` and counts sample frames only by decoding them.

World's answers are standard SPARQL results, and what each projection version
emits is declared in [`world/projection/`](../world/projection/6/projection.json)
as SHACL shapes; [`fixture/`](../fixture/README.md) holds synthetic records
and a builder that admits them through Door, so any client can build the same
archive ([ADR 0059](decisions/0059-world-read-contract.md)).

`door admit --proposal <directory> --dry-run` predicts the exact revisions an
admission would create without writing or transferring anything. The proposal
directory interface is accepted. Persistent admission commits metadata-only
batches under the archive lock; new content-bearing batches remain explicit
unsupported behavior until the required git-annex experiment, while complete
retries return without transfer or mutation.
A specification upgrade is a `door transition` proposal of one
`archive.transition/specification_upgrade` record, written under the target
specification and authorized by the current revision of the active operator's
appointed person ([ADR 0068](decisions/0068-specification-upgrade.md)). Door
adopts only the release it carries, and only when that release is additive
for the archive's own; it takes the release's documents and bundles from its
own specification files, never from the proposal. One commit under the archive
lock adds the record, `spec/<version>/`, and the added bundles, and replaces
`archive.json` with the new version; a retry writes nothing, and no downgrade
exists. The command is the same everywhere, including `door serve`; running it
against the shadow or a real archive needs its own approval
([shadow runbook](shadow-runbook.md#specification-upgrade)).
`door transition --proposal <directory> --dry-run` likewise predicts accepted
retire/destroy transitions, validates durable version-2 batch order, and
reports the authorization and state fences that must be rechecked by the
persistent command. Without `--dry-run`, it rechecks those fences under the
archive lock and records the complete transition batch in one Git commit,
without invoking git-annex or staging unrelated files. It replays immutable
committed history to recover the
active operator, object heads, and retire/destroy state. Persistent mutation
pins the archive root and `.git` directories and spawns Git from the opened
`.git` directory through a small POSIX helper; pathname replacement cannot
redirect that child. It synchronizes installed records, the reachable local Git
object baseline, and the advancing ref, and reconciles journaled interrupted
record installations under the archive lock. A batch is built in a temporary
Git index; once its ref has advanced, reconciliation, in the committing process
or the next Door after a kill, refreshes the ordinary index to the commit only
while it still holds the journaled old commit's tree. Any other staged change
is left in place and refused. `archive init` also holds that
lock through its initial commit and synchronization barrier. Its durable
ownership journal can finish a fully installed, matching pre-ref initialization
after an interrupted commit; an incomplete layout or mismatched journal is
retained and refused for operator inspection. These mechanisms
do not yet establish a verified power-loss durability acknowledgement under
[ADR 0031](decisions/0031-power-loss-durability.md); filesystem power-loss
behavior needs validation. `archive maintenance prune-packed` is the first supported
operator maintenance command: it holds the archive lock, verifies each existing
pack, synchronizes the complete ref-reachable object baseline before pruning,
proves refs stayed unchanged, then verifies and synchronizes the resulting
baseline. It removes loose copies only; it does not create or replace packs.
The current Git seam refuses shallow or non-linear history
because it cannot yet prove a complete root-to-snapshot order; that is an
explicit incomplete-read boundary, not a claim that such repositories contain
no evidence.
Separate, well-named commands are preferred over one command that does
everything.

## Left out on purpose

These are not oversights. Each would be a large commitment made before any real
need, and each has a simpler path through the concepts above. Proposing one
requires a decision record that names the concrete need.

- a workflow engine, distributed scheduler, or Kubernetes-style controllers
  (Spool distributes work; plan and apply reconcile);
- a graph database, an RDF framework, or an ontology of everything (World uses
  existing engines and vocabularies);
- a home-made query engine for policy (clingo evaluates it);
- plugin or dependency-injection frameworks, a generic repository layer, an SDK,
  or a shared library across sibling projects;
- an event bus, a service mesh, speculative microservices, or daemons without a
  need;
- a universal media model;
- resistance to compelled disclosure, such as duress passwords or hidden
  volumes (a compelled disclosure is recorded; encryption at rest is
  git-annex's);
- a web application (the review app is a sibling project);
- compatibility layers for formats that do not exist yet;
- placeholder implementations that only make the repository look bigger.

A small repository is the goal. An obviously missing piece is better than a
misleading abstraction.

## Where new code goes

1. Name the concept it serves. If none fits, apply the
   [new-noun test](vocabulary.md#adding-a-noun) before creating anything.
2. Create a top-level directory for a concept only when it has real code with a
   contract. Concepts that are only documented stay in `docs/`.
3. Keep configuration and policy in separate files with separate names.
4. Keep regenerable state out of the source tree.
5. When porting from the V2 spike, translate its names into this vocabulary
   ([ADR 0015](decisions/0015-porting-from-v2.md)).
