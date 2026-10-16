# 0004. Deletion: retire and destroy

Status: accepted

A curated archive must be able to let things go, and must still be able to
explain, years later, what went and why.

## Decision

Deletion is two Door transitions, always initiated by a person.

**Retire** acts on an object. It leaves the collection; its records stay. A
duplicate import is retired while its shared bytes survive.

**Destroy** acts on content. It withdraws the bytes from archival custody and
requires their deletion from every copy the archive controls. Door refuses to
destroy content that any unretired object still refers to. A terabyte of junk is
retired and destroyed in one command.

Destroy separates the decision from its execution:

1. **Destroyed**: authoritative the moment it is recorded. The content no
   longer counts as a copy anywhere and may never be placed again.
2. **Deletion pending**: copies that are offline (a cold drive in a drawer) are
   deleted the next time plan and apply reach them; `archive status` lists them.
3. **Deletion complete**: every controlled copy is gone.

Copies the archive knows about but cannot control, such as a photo service, are
reported as **external survivors**. They do not block completion, but the
record says they exist. "Destroyed" never quietly means "gone everywhere".

**Nothing destructive happens implicitly.** When content is destroyed, the plan
lists every dependent with bytes of its own (an MP3 rendered from a WAV, say),
and each is decided in the transition: destroy it too, or keep it and record
that its source is gone. `--cascade` destroys all of them. Claims without bytes
(a label, a transcript's metadata) stay by default because they are history.

**Unattended work never destroys.** Apply carries out only destroy transitions a
person recorded. Policy may recommend candidates; it never initiates them.

**Three different things.** Destroy is content destruction. It is not
**information erasure** (removing what derived evidence reveals about destroyed
content, such as a verbatim transcript or a face embedding) and not
**expungement** (erasing records and their Git history). Privacy-driven deletion
may need all three; only destroy is designed so far. Expungement is rare and may
be done by hand with Git until it is designed.

## Rejected

- **Automatic cascade.** Keeping a transcript after deleting the WAV is right
  for storage and wrong for privacy; the person deciding must choose.
- **Rewriting Git history as normal deletion.** It breaks every clone and every
  commit reference, so it stays an exceptional, manual procedure.
