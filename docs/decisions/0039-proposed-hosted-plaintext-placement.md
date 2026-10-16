# 0039. Proposed hosted plaintext managed placement

Status: proposed

The operator chose a plaintext whole-Archive copy on the seedbox and requires a key
or password at every served interface, including the folder previously called
"public." This resolves the encryption-versus-plaintext preference but does
not by itself authorize any content transfer. The seedbox is hosted infrastructure:
authentication for web visitors does not prevent its provider from reading
plaintext. ADR 0016 therefore classifies the placement as service disclosure.
Accepted ADR 0032 currently limits the first placement implementation to
private managed copies and leaves cross-custody placement unimplemented. This
record proposes the narrow change needed before proposed ADRs 0037 and 0038
can be accepted for the seedbox.

## Proposed decision

Archive MAY ensure a managed plaintext copy at a hosted service-class location
only when policy grants disclosure of **every content item** in the bound
snapshot to that destination class, each item's sensitivity permits it, and
Door has recorded the disclosure before the first byte of that item
is sent. A missing or unprovable grant blocks that item; the result MUST NOT be
reported as a whole-Archive copy. A failed or interrupted transfer keeps its
pre-send disclosure record, because bytes may have left. Location verification
and copy-count rules remain those of ADRs 0009 and 0032; service disclosure
does not itself make a verified managed copy, and the operation cannot drop a
source copy.

This proposal would supersede only ADR 0032's limit of the initial placement
implementation to private operator-controlled locations. It would retain
ADR 0032's additive ensure operation, stable location identity, authenticated
SSH binding, independent fixity verification, and separation of publication
from managed presence. The exact disclosure claim fields and policy syntax
remain open in [the open questions](../open-questions.md); no real seedbox apply
may run before those contracts are accepted and installed.

The music folder outside the annex repository remains a publication
projection, with no managed-copy credit. Its viewers and the hosted provider
are distinct audiences. Every HTTP or other served route to seedbox content,
including paths labelled "public," MUST require an authenticated key or
password before serving any bytes or revealing private listing information.
There is no anonymous publication route in this seedbox profile. Publication
still requires the exact-version approval and rights checks of ADRs 0010 and
0019; authentication does not supply that approval and does not make the seedbox
private from its provider. The operator must identify the permitted viewer
class and credential management before any new projection is enabled.

## Required conformance before a real transfer

- A complete source manifest proves the exact Git state and all required annex
  keys; an ungranted or sensitivity-blocked item prevents a full-copy result.
- Door commits each applicable disclosure before sending that item's bytes,
  and an interrupted send leaves the record intact.
- A verified destination contains the matching Git history and every required
  annex item; a matching retry is quiet under INV-5.
- Unauthenticated requests to every served route, including the music folder
  and listings, receive no content; authenticated access remains scoped to the
  operator-approved viewers and exact versions.
- A projection never increases the managed copy count, and no placement path
  publishes, drops, or destroys content.

This proposal changes no deployed seedbox service, credential, or Archive data.
It does not settle the disclosure claim encoding, policy grammar, web
authentication implementation, or the operator's per-content grants.
