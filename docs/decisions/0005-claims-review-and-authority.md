# 0005. Claims, review, and two kinds of authority

Status: accepted

The archive will hold claims from people, models, deterministic tools, and
outside sources, and they will disagree. It needs a way to judge them without
losing any, and a clear answer to who may cause consequences.

## Decision

**Annotation and Review are different acts.** Annotation is a person asserting
a claim ("this recording is Paper Lanterns"). Review judges an existing claim against
evidence and produces an assessment. One gesture in an interface may do both,
but the archive records two things: a new claim, and a judgment of a specific
claim revision. Review need not be human: a checksum comparison, a second
model, or a classifier vote can review.

Review has two verbs: to **assess** is to judge one claim; to **adjudicate** is
to settle between competing claims. Both produce assessment records, and
neither erases what it judged.

**Disagreement is kept.** Conflicting claims coexist, each with its provenance.
Storage never decides which is true; an assessment is a further record, never
an overwrite. That a classifier was wrong, and how it was caught, is itself
worth keeping.

**Two kinds of authority.**

- **Evidentiary authority** is how strongly a claim should be believed. It
  depends on the kind of claim and its provenance. There is no ordering of
  people over machines or the reverse: matching hashes settle byte identity
  better than any listener, and a performer naming the song settles its identity
  better than a weak classifier. There is no universal confidence scale; each
  producer's uncertainty is kept in its own terms.
- **Governance authority** is who may cause a consequence: authorizing policy,
  destroying content, approving publication, acting on a reclaim verdict. It
  belongs to the operator and may deliberately require a person.

Neither substitutes for the other. A machine claim can be the best evidence
available and still authorize nothing; a person's approval makes nothing truer.

**Author and authorizer are different facts.** For governance-gated records,
the archive records who proposed (the author, possibly a model), who authorized
(the operator), and which program wrote the record. Authorization never
relabels a machine's work as a person's.

**Legacy review states become assessments, honestly.** The predecessor stored
review outcomes as states inside claims (`reviewed`). They are translated into
assessment records that cite the record they came from, with an unknown
reviewer or time left unknown. No assessment is created where the source
recorded no review.

## Rejected

- **A universal human-over-machine ranking.** Wrong in both directions,
  depending on the question.
- **Review states stored inside claims.** They conflate the claim with a
  judgment of it, and lose which revision was judged.
