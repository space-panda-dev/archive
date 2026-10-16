# 0016. Disclosure: content leaving the operator's custody

Status: accepted

Publication covers content that others can see. It does not cover content sent
somewhere to be worked on: a recording sent to a hosted transcription model, a
batch run on a rented GPU, a job on a friend's machine. Each of those hands
record content to someone else, and an archive that may one day hold a diary or
a therapy session has to govern it.

## Decision

**To disclose is to let content leave the operator's custody.** Publication is
one kind of disclosure. The other is sending content to a **processor**: a
destination that receives content in order to compute on it and shows it to no
one else. A processor is a destination like any other: where it is and how to
reach it is configuration, its exposure class is policy, and a processor with no
declared class is treated as distribution. Computation on the operator's own
machines is not disclosure.

**Disclosure is about plaintext.** Storing content encrypted with a key the
provider never holds, as with an encrypted git-annex remote, is not disclosure.
Computing on content needs plaintext, so sending it to be processed always is.

**Processors take the class of whoever can see the content.** A friend's
machine shared through Spool is circle. A hosted model provider, a cloud
server, or a rented GPU in a provider's data centre is **service**: the host can
read memory, so having root does not make it private. A broker that routes
between providers is service only when pinned to named providers with fallback
disabled; otherwise it is undeclared. A processor whose terms let it keep
content or train on it is distribution, not service: a model trained on content
carries it to everyone who uses the model. A marketplace of machines run by
strangers is open. A spool is a destination too: putting a task on one
discloses its payload and attachments to everyone who can lease from it, so
each spool Archive uses has an exposure class, and a spool shared with a friend
is circle. What a spool's workers are configured to run never narrows
that: every grantee who can lease from the spool can read what is put on it,
whichever worker Archive expects to run the task.

**Disclosure is denied unless policy grants it.** Policy rules grant disclosure
of content to a destination class, selecting on evidence such as kind, origin,
and source ("rehearsal audio may go to the transcription service"). Grants only
add, as all policy does (ADR 0007). A fact that cannot be established never
widens disclosure.

**Sensitivity is a claim, not a property of a kind.** A single claim kind can
cover a band rehearsal and a therapy session, so sensitivity is never attached
to a contract. A person may record a sensitivity claim on an object using the
Traffic Light Protocol 2.0 labels, and each label sets the most exposed class
the object may reach:

| Label | Furthest it may go |
|---|---|
| RED | private |
| AMBER | circle |
| GREEN | service |
| CLEAR | open and distribution |

A label only limits; it never grants. Door refuses a disclosure beyond an
object's label, as it refuses a publication without permitting rights.

**Every disclosure is recorded,** even when the result is discarded, and
before any content is sent: no transaction spans Door and a remote service, so
a send that may have begun counts as received. A disclosure record means the
content may have left; it can overcount, never undercount. The record
names the destination, the content, and the policy revision that allowed it. A
processor that received content is a place a copy may persist, so destroy
reports it as a possible external survivor (ADR 0004). Disclosure compelled by
law, such as a court order or a search, is not granted by policy; when content
leaves that way it is still recorded, with the order as its reason.

**Archive governs only the channels its own code opens.** Enrichment adapters,
and any read adapter this repository later provides, check disclosure before
sending content. The operator's interactive tools (a coding agent run over an
archive's files, for example) are the operator's own acts and outside Archive.
Tools that live elsewhere, such as Spool or agent tooling, reach archive content
only through an adapter from this repository, and so through its checks.

## Rejected

- **Treating a rented machine as private.** Whoever runs the hardware can read
  what it computes on.
- **A separate family of channels** (operator read, local computation, external
  model). Destinations and exposure classes already describe who can see the
  content; a second vocabulary would repeat them.
- **A sensitivity label on each contract.** It is wrong for any kind whose
  content varies, which is most of them.
- **Recording only disclosures whose results are kept.** The content left either
  way, and destroy needs to know where it went.
- **Guarding the operator's own filesystem access.** Archive cannot enforce it,
  and pretending to would be worse than saying so.
