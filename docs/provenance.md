# Provenance

An archive meant to last decades will outlive the programs, models, and people
that produced its claims. Provenance is what lets a later reader decide how far
to trust any of them, so it is part of the architecture, not decoration. The
decisions behind this page are in
[ADR 0005](decisions/0005-claims-review-and-authority.md).

## What a claim must be able to say

The record format is defined by the
[specification](../spec/archive-spec.md#4-format-and-protocols). A record must
be able to say, where relevant:

- **who or what asserted the claim** (`agent`), separately from **which program
  wrote the record** (`by`);
- **from what**: the evidence it was derived from, pinned to exact revisions;
- **how**: the model or method, its version, and its configuration;
- **when** it was produced;
- **how sure**: uncertainty in the producer's own terms;
- **how it has been judged**: its assessments;
- **whether it has been replaced**, and by what;
- **what caused it**, for a consequential transition: the policy revision, the
  rules that fired, the facts they matched, and the specification version in
  force;
- **who proposed and who authorized it**, for anything that needs the
  operator's authority.

A claim that could have carried its provenance and did not is a defect, even if
the claim is correct.

## No ranking of sources

Claims come from different kinds of source:

- a person (an annotation, a participant's testimony, the operator's judgment);
- a model (a classifier, a transcriber);
- a deterministic computation (a hash, a duration, a sample rate);
- an outside authority (MusicBrainz, Wikidata, a catalogue).

None outranks another in general. Which is strongest depends on the question: a
hash comparison settles whether two files are identical better than any
listener; a performer naming the song settles which song it was better than a
weak classifier; MusicBrainz may be the best source for a composition's title and
the worst for what was played at a particular rehearsal.

So there is no universal confidence scale. A classifier's 0.72 and a person's
"pretty sure" are kept as each producer expressed them.

This is about belief. Who may *cause* something (publish, destroy, change
policy) is a different question, and the answer is the operator.

## Credits

A credit's provenance is its asserter
([ADR 0078](decisions/0078-contributions.md)):

- a booklet, Discogs, or MusicBrainz credit pins the admitted document, keeps
  the source's own wording and the name as credited, and may say where it was
  retrieved and when;
- the operator's credit ("this player played keys on this take") names the
  operator as its agent;
- an instrument detector's claim pins its run, keeps its confidence in its
  own scale, and names no contributor: it can locate a person's credit, never
  become one.

Each source's credit is its own claim; none is merged into another or
skipped because another source agrees. Sources that copy each other agree
without being independent, so nothing counts agreement. A finer credit
refines a coarser one whoever asserted either, and the resolved view says why.

## Disagreement is evidence

The archive must be able to hold, at the same time:

```
source A says X
source B says Y
a person says Z
```

without its storage deciding which is true. Review judges; World helps explore;
"current" reports a conflict rather than choosing. An assessment is a new record
judging the others and never deletes them. That a classifier was wrong, and how
it was caught, is worth keeping.
