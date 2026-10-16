# 0010. Publication: destinations, approval, and rights

Status: accepted

Publishing means making something visible to others: recordings on a seedbox for
bandmates, a book on a website, a radio stream on a mesh network, an album
through a commercial distributor. These differ in who can see the result, whether
it can be taken back, and whose work it is. None of this is specific to audio.

## Decision

**Publication is placement somewhere others can see.** It usually implies a
derivation (the rendition to publish must exist), and publishing and withdrawing
are recorded transitions. Where the destination holds a managed copy, such as the
seedbox, publication is a projection: a symlink into the media server's library.
Published copies never count toward placement.

**Every destination has an exposure class.**

| Class | Example | Character |
|---|---|---|
| private | the operator's own devices | only the operator |
| circle | a seedbox with accounts made for named people | people chosen one by one |
| service | a mastering house, a hosted model, a rented server | an organisation bound by its terms |
| open | a website, a mesh-network radio station | anyone who finds it |
| distribution | a commercial distributor feeding stores and streaming services | public, commercial, hard to take back |

Where a destination is and how to reach it is configuration. Its class is
policy, authorized by the operator. A destination with no declared class is
treated as distribution.

**Approval is per destination.** A publication depends on an approval by a
person, recorded as an assessment whose scope names the destination (or, if the
operator chooses, a whole exposure class) and whose references pin the exact
versions published, never "latest". An approval never reaches beyond its scope. Once approved, publication runs
unattended; revoking the approval withdraws it. Door refuses a publication that
does not depend on such an approval.

**Own work and others' work differ.** The `origin` claim decides:

- **the operator's own work** publishes unattended once approved;
- **anything else, including unknown origin,** needs a fresh approval, given at
  the time, before it goes beyond the operator's circle.

**Rights are claims with terms.** Origin (own capture, field recording,
commercial release) and rights are separate claims. Rights use the
RightsStatements.org vocabulary and record their terms: jurisdiction and dates.
A composition and a particular recording of it have separate terms. Unknown or
restrictive rights never widen exposure: open and distribution destinations
require a rights claim that permits them. When a term ends, the work becomes
eligible and the plan recommends it; it never publishes on its own.

**Distribution cannot be fully undone.** For distribution destinations, the
plan says the destination is irreversible before approval is given, and a
withdrawal is recorded as requested, with known outside copies reported, never
assumed gone.

## Rejected

- **A separate permission record.** Nothing in the predecessor used one;
  scoped approvals and policy-caused transitions already carry the authority.
- **Publishing automatically when rights expire.** A work entering the public
  domain is not a decision to publish it.
- **One approval covering every destination.** A seedbox for bandmates and a
  streaming service are different decisions.
