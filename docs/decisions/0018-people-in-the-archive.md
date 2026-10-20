# 0018. People in the archive who are not its operator

Status: accepted; amended 2026-10-19 by
[ADR 0096](0096-origin-of-recordings.md) (a depositor's limits bind like a
recorded person's; accepted by the operator, 2026-10-19)

An archive records other people: bandmates in a rehearsal, a friend in a diary,
a therapist in a session. The operator governs the archive (ADR 0005), and
until now nothing gave those people any say in where content that records them
goes. Archives governed only by whoever holds them have harmed the people in
them; mass leaks that exposed bystanders are the familiar case. Community
archives answer this with access protocols set by the people the material is
about.

## Decision

**A person the content records, or who deposited it, may limit its
disclosure.** Their limit is a sensitivity claim (ADR 0016) whose source is
that person: "Sam: AMBER on the rehearsals Sam plays in". The operator records it, and its provenance names the
person as its author, never the operator. It caps disclosure, including
publication, exactly as the operator's own label does. No key or account is
needed: people who never touch a computer still have preferences, and the
archive's physical collections record them too.

A depositor is the person whose library the archive took the content
from, named in the `depositor` role of a head origin claim (ADR 0096)
that the operator asserted or affirmed, or that arrived in the same
admission as the content, and that the operator has not rejected. Custody
of someone else's material is held on their behalf, so they keep a say over where it goes even when it
does not record them. Their limit is a sensitivity claim whose source is
that person, it binds exactly as a recorded person's does, and only they
lift it. Being a depositor gives no authorship and no consent over
sharing; it gives only the limit. If the operator later rejects the
origin claim, the depositor can set no new limit through it, and a limit
already set still binds until its author lifts it. (Added by the
amendment of 2026-10-19, [ADR 0096](0096-origin-of-recordings.md); the
rules below apply to depositors unchanged.)

**A limit's author is a governance fact.** Who may lift a limit follows the
claim's agent, so authorship here is more than provenance, and no separate field
restates it. Legal restrictions are rights (ADR 0010), never sensitivity.

**The most restrictive claim wins.** An object may carry several sensitivity
claims; Door refuses any disclosure beyond the strictest. A label only limits,
so no one's claim can widen another's.

**Only the person withdraws their limit.** A limit is lifted by a later claim
from the same person, never by the operator or by policy. Both claims are kept.

**The operator may go past a limit once, with a reason, and never to the
public.** A limit binds policy and all unattended work. The operator may
disclose past it to one named destination, no further out than service, with a
recorded reason: a recording of someone who abused the operator, given to a
lawyer or the police, should not be stopped by that person's own label. The
limit stays in force for everything else, and the record shows it was
overridden. Going past a limit is a disclosure, recorded as one; it never
changes the limit. Afterwards the archive still answers "Sam's limit is
circle", never "this content's exposure is now service". Nothing goes past a person's limit to open or distribution. The
operator's own copy is private, the lowest class, so no limit ever takes the
operator's own memory from them.

**What this does not do.** Archive cannot make the operator record a limit, and
it gives the people in an archive no right to read or erase it. Until a limit
can be signed by its author, nothing but the operator's honesty stops them
recording a false withdrawal. Archive makes a recorded limit binding on
everything it does, and every departure from it visible. That is the most a tool
held by one person can honestly promise.

## Rejected

- **A limit no one can go past.** It would let an abuser's label keep the
  evidence of the abuse from a court.
- **Advisory limits that only warn.** A warning is a limit that binds nothing.
- **Operator sovereignty alone.** It treats the people in the content as the
  operator's property.
- **Access and erasure rights for everyone recorded.** Too heavy for a personal
  archive, and Archive could not enforce them against its own operator.
- **A separate consent mechanism.** Sensitivity claims already limit
  disclosure; a second mechanism would repeat them.
