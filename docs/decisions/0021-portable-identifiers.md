# 0021. Portable archive and object identifiers

Status: accepted

Object identifiers must survive moves, renames, changed bytes, and the loss of
every hostname. They must also remain globally unambiguous when records from
two archives meet. The predecessor's filename-safe local tokens are useful
identity inputs, but bare tokens collide across archives.

## Decision

An archive identifier is `urn:uuid:<archive-uuid>`, where `archive-uuid` is a
canonical lowercase RFC 9562 UUID created by `archive init`.

An object identifier is `urn:uuid:<object-uuid>`. Door derives `object-uuid`
with RFC 9562 UUIDv5, using the archive UUID as the namespace and the UTF-8
sequence `identity namespace`, NUL, `identity name` as the UUID name. The
namespace is owned by the applicable shape or contract. An identity name is an
opaque, non-empty ASCII token restricted to letters, digits, hyphen, and
underscore. The full URN, not the identity name, is stored as `object_id` and
as a reference's `id`. ADR 0026 defines allocation and namespace syntax.

UUIDv5's SHA-1 use is namespacing, not an integrity claim. Object identity
does not become content identity, and weakening of a content digest algorithm
does not affect object identifiers. A contract must never derive an identity
name from mutable observations such as a path, filename, title, location, or
content digest.

Content digest fields use `sha256:` followed by 64 lowercase hexadecimal
digits. This is the record spelling of the digest required by P-6. Its RFC
6920 equivalent is the `ni:///sha-256;...` URI formed from the same 32 digest
bytes in unpadded base64url. P-10 means this mechanical equivalence; records do
not carry two content identifiers. This resolves the provisional mismatch
between P-6's and P-10's notations without changing their algorithm.

Migration keeps a predecessor identifier in `source`. It becomes an identity
name only when an accepted identity contract establishes that it named the
enduring object. A name, path, or matching digest is never enough. Where no
such rule exists, migration reports the identity as unresolved rather than
minting a relationship it cannot support (ADR 0015, INV-24).

## Why this is the smallest identifier scheme

`urn:uuid` is standardized, non-resolvable, contains no hostname, and needs no
registry maintained by this project. Namespacing the deterministic object UUID
under the archive UUID prevents two archives from colliding; including the
identity namespace prevents two contracts inside one archive from colliding on
the same local name. Keeping the name opaque leaves domain identity with its
owner, while the envelope needs only one syntax (S-9, S-14, ADR 0012).

The predecessor provides evidence that filename-safe, contract-owned identity
tokens make deterministic migration and idempotent retries practical. It does
not justify reusing its bare tokens as portable identifiers.

## Alternatives rejected

- **A project-specific `urn:archive:` namespace.** It would invent an
  unregistered URN namespace when standardized UUID URNs already meet the
  requirement.
- **A `tag:` URI.** Its authority form is unnecessary and invites the hostname
  dependency S-14 prohibits.
- **A bare local token.** It can collide with the same token in another archive.
- **A content hash.** Changing an object's bytes would change its identity,
  contrary to S-9.
- **A hostname, URL, or filesystem path.** It is a location that will not
  outlive the machine and is prohibited by S-14.
- **Store both `sha256:` and `ni:` forms.** They encode the same digest and can
  disagree. One canonical stored spelling plus a defined conversion is enough.
