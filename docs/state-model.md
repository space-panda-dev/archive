# State model

Every piece of state belongs to one class, and the class follows from what
losing it would cost, not from which folder it sits in. A cache inside an
archive's directory is still a cache. An admitted transcript is evidence, even
though a machine could produce another one.

## Evidence

Loss is loss of information. Evidence changes only through Door.

Evidence arrives in two ways:

- **as original or asserted**: admitted files, entities, annotations,
  assessments, policy revisions, records of consequential transitions;
- **as derived and deliberately kept**: machine claims, transcripts, analyses,
  derivatives, correspondences promoted from World.

A derived result could in principle be recomputed, but a recomputation is a
different result: a different model, a different day, different inputs. Once
admitted, both kinds behave the same way. Do not build separate code paths for
them unless their behaviour actually differs.

A contribution or band membership claim is evidence whoever asserted it; the
booklet scan or saved Discogs or MusicBrainz response it cites is admitted
first, as an Artifact. The resolved view of credits, which places finer
claims under coarser ones, is regenerable
([ADR 0078](decisions/0078-contributions.md)).

## Regenerable state

Loss costs time and computation, not history. Examples: indexes, query
databases, the "current" projection, views, caches, thumbnails, World's store,
and the derived-media root of waveform peaks and listening copies
([ADR 0069](decisions/0069-derived-media.md)), which `enrich/media.py` makes
again from the content.

Regenerable means rebuildable in principle, not cheaply: rebuilding World may
take days and need outside data. Regenerable state never changes through Door
and is never the only copy of anything above.

## Operational state

Bookkeeping a program keeps to do its job: routine verification results, plans
in progress, leases, retry counts. It belongs to that program and can be lost.
The staging repositories that `enrich/` and `capture import` add content to
before proposing it are operational too: Door copies what it admits into the
archive, and content a failed admission left staged is only a copy of its
source ([ADR 0023](decisions/0023-admission-and-staging.md)). A recorder
card is a source, not staging: it is cleared only by the operator.
A policy may be evaluated millions of times without writing anything; only a
consequential transition leaves evidence.

## Deciding a class

1. Would losing it lose information that exists nowhere else? It is evidence.
2. Was it deliberately admitted as a historical result? It is evidence.
3. Otherwise it is regenerable or operational, and code must survive its loss
   at any moment.

Turning regenerable output into evidence is an admission through Door, never a
move to another folder. Whether a kind of enrichment output is kept is a policy
decision.

## The ownership test

For any index, projection, database, or cache, ask:

> If this disappeared, where would the authoritative information still exist?

If the answer is "nowhere", it has become authoritative by accident. That is a
defect. The fix is to admit the information as evidence, not to start backing
up the cache.

## Where copies sit

Content can sit in many places. Each is a **managed** copy, an **observed**
copy, a **projection**, or a **source**, and only some of them count toward
keeping content safe. See [ADR 0009](decisions/0009-copies-and-custody.md).

## Loss and rebuild

What is lost if a part's entire runtime state disappears? This is the expected
answer, not a disaster-recovery design.

| Part | Lost | What survives |
|---|---|---|
| Door | proposals in flight, staging | every recorded transition |
| World | its store and indexes | everything it was built from; rebuilding costs time and perhaps outside downloads |
| Enrichment | caches, intermediate output, derived media | admitted results and the content; the rest is recomputed |
| Annotation | application state, drafts | accepted annotations |
| Review | application state, queues | recorded assessments |
| Policy | evaluations, the "current" projection | every policy revision |
| Plan / apply | plans, progress | nothing is needed; plans are recomputed |
| Spool | outside this project | defined by Spool |

An implementation that makes any row false is wrong, not the table.
