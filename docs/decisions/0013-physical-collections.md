# 0013. Physical collections

Status: accepted

A life archive holds things as well as bytes: records, books, clothes,
handwritten notes, tapes. The archive cannot keep a physical object, but losing
the record of a collection is losing information.

## Decision

**The archive keeps the record of the collection.** A physical item is an
entity of kind Item.

- **Admit** accessions it; **retire** deaccessions it (sold, given away,
  discarded, lost), with a reason. The meanings are those of library accession
  and deaccession, mapped to the Spectrum collections-management procedures.
- Everything else about it is claims: where it is, its condition, which edition
  or pressing it is, how it was acquired.
- Scans, photographs, and digitisations are artifacts derived from the item and
  pinned to it. Policy can require them ("tapes digitised in two failure
  domains").

**Byte rules govern digital content.** Destroy, copies, failure domains, and
placement apply to digital content only. A physical item's custody is the
operator's; the archive's custody is of its record and its digital surrogates.

In the library reference model (ADR 0012), a vinyl record and its FLAC rip are
both Items of the same release. In the record model they differ: the vinyl is
an entity, because the archive cannot keep it; the rip is an artifact, because
the archive keeps its bytes. Both map to the library model's Item, so they are
siblings, not strangers.

## Rejected

- **PREMIS for physical objects.** It is built for bytes; nearly every useful
  field would be inapplicable.
- **A separate system for physical collections.** It would split the half of a
  life archive that cannot be regenerated from anywhere.
