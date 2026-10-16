# 0026. Initialization bootstraps one archive and one operator

Status: accepted

The proposed layout gives `archive init` files to create, but not an exact
manifest, an initial operator, or a collision-free way to allocate object
identifiers. Without those, the first person cannot authorize a transition and
Door cannot verify that a new object belongs to this archive. This proposal
defines creation only; later succession and its verification remain open.

## Decision

`archive init --operator <identity-name> <root>` creates an archive only when
`root` does not exist or is empty. `identity-name` is an opaque token supplied
by the person creating the archive; it is not a display name. Initialization
generates a random RFC 9562 UUIDv4 for the archive and writes this closed
manifest in RFC 8785 canonical form:

```json
{
  "archive_id": "urn:uuid:<archive-uuid>",
  "layout": "archive/1",
  "spec": "archive/0.0.1"
}
```

Initialization creates the layout in ADR 0020, initializes Git and git-annex,
copies the governing specification, core context, and all specification-owned
contract bundles, and makes one initial Git commit. That commit includes a
person entity for the operator and an
`archive.transition/operator_initialization` version `1` claim that appoints
it. The claim has pinned `author`, `authorizer`, and `appoints` references to
the person revision; the same person fills all three bootstrap roles, and
`agent` names that person. This is the only transition allowed without a
previous active operator.

The active operator is derived from the initialization claim and later valid
succession claims, never copied into `archive.json`. Until succession
verification is defined, Door refuses every succession attempt, so the initial
operator remains active.

The operator entity uses namespace `archive.core/entity` and the supplied
operator identity name. The initialization claim uses namespace
`archive.transition/operator_initialization` and the fixed identity name
`initial`. The specification also reserves `archive.core/artifact` for core
artifact objects. Other core entity objects use `archive.core/entity`; a
contract-owned object uses that contract identifier as its namespace. Identity
names must be unique within a namespace for the enduring objects they name.

If `root` is already the archive produced by the same initialization inputs,
`archive init` returns its archive and operator identifiers and writes nothing.
If the root is non-empty but not that archive, its manifest differs, its
bootstrap records are incomplete, or the requested operator differs, init
refuses and changes nothing. Failure before the initial commit removes only
paths init itself created; it never cleans a pre-existing root.

### Object allocation

Every new object proposal supplies an identity allocation
`{object_id, namespace, name}`. `namespace` is a stable portable identifier
owned by the shape or contract, and `name` is a non-empty ASCII token containing
only letters, digits, hyphen, and underscore. Door derives the object UUID with
UUIDv5 using the archive UUID as namespace and the UTF-8 byte sequence
`namespace`, NUL, `name` as the UUID name. The supplied `object_id` must equal
the resulting lowercase `urn:uuid:` value.

The admission manifest proposed by ADR 0023 therefore gains a required
`identities` array, including when empty. It has exactly one allocation for
every object new to the archive and none for an existing object. The allocation
is operational input: the stable full object URN is evidence, while the token
used to mint an opaque identifier is not independently meaningful.

Specification-owned namespaces begin `archive.`. Their syntax, and the syntax
of contract identifiers and `source.schema`, is one or more lowercase ASCII
segments separated by `/`; a segment starts with a letter and continues with
letters, digits, dot, underscore, or hyphen. These identifiers contain no URI
scheme, authority, path traversal component, or hostname.

`by` is a canonical Package URL identifying the writing program and version.
`agent` is an Archive object URN for a person, organisation, software, model,
or process entity, or a canonical Package URL for packaged software, a model,
or a process. A Package URL used by Archive must have no qualifier, subpath,
URI authority, repository URL, download URL, or component parsed as a DNS
hostname. An unpackageable agent is represented by an Archive entity; Door
never substitutes a hostname or command line.

Migration uses a translation-owned namespace and a source identifier as its
identity name only where an accepted translation rule establishes enduring
identity. Otherwise the row remains unresolved (ADR 0015, INV-24).

## Why bootstrap is a distinct rule

At creation there is no prior operator who could authorize the first operator.
Making that one boundary explicit is smaller and more auditable than hiding an
operator in mutable configuration. Every later change still needs the
succession transition and verification required by S-3. The identity namespace
in UUIDv5 prevents two contracts using the same local token from naming the
same object.

The predecessor provides evidence that deterministic object allocation,
idempotent initialization, installed contracts, and an initial Git commit are
workable. It is not authority for operator identity or succession.

## Alternatives rejected

- **Store the current operator in the manifest.** It would duplicate a derived
  transition result and could change outside Door.
- **Allow init on an arbitrary non-empty directory.** Partial ownership makes
  rollback destructive and lets existing bytes masquerade as an archive.
- **Use only the local identity token as the UUIDv5 name.** Two identity
  domains issuing the same token would collide.
- **Let a caller supply an unchecked object UUID.** The archive namespace would
  no longer prevent collisions or establish provenance for allocation.
- **Put the identity token in every record.** The full object identifier is the
  durable identity; retaining allocator input would expose a second identity.
- **Use a hostname for programs or agents.** Machines move, and S-14 forbids
  hostnames in agent identifiers.
- **Treat bootstrap as succession.** There is no predecessor operator to
  verify; conflating them would appear to answer the still-open succession
  question.
