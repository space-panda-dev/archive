# 0029. The core context and bootstrap contract are exact local resources

Status: accepted

ADRs 0025–0027 require a core JSON-LD context and an
`operator_initialization` contract, but they do not define the Archive-owned
IRIs or the bootstrap claim's payload. Implementation cannot mint either
without creating archival meaning outside the specification. ADR 0012 also
names `oa:generator` and `oa:creator`, but those properties do not exist in the
W3C Web Annotation vocabulary: the Recommendation uses `as:generator` and
`dcterms:creator`.

## Decision

Archive owns one permanent vocabulary namespace:

```text
urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#
```

The UUID is an opaque namespace identifier, not an archive identifier and not
a network location. A term not mapped directly to an adopted vocabulary uses
that namespace plus its JSON field or controlled value. The core context ships
these prefixes:

| Prefix | IRI |
|---|---|
| `arch` | `urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#` |
| `as` | `http://www.w3.org/ns/activitystreams#` |
| `dcterms` | `http://purl.org/dc/terms/` |
| `prov` | `http://www.w3.org/ns/prov#` |
| `rdf` | `http://www.w3.org/1999/02/22-rdf-syntax-ns#` |
| `rdfs` | `http://www.w3.org/2000/01/rdf-schema#` |
| `xsd` | `http://www.w3.org/2001/XMLSchema#` |

The core context maps `by` to `as:generator`, `agent` to
`dcterms:creator`, and `at` to `prov:generatedAtTime` with
`xsd:dateTime`. `kind` maps to `rdf:type`, and `label` to `rdfs:label`.
The values `person`, `organisation`, and `software` map to `prov:Person`,
`prov:Organization`, and `prov:SoftwareAgent`. `model` and `process` map to
the stricter Archive terms `arch:ModelAgent` and `arch:ProcessAgent`, each
profiled as a `prov:SoftwareAgent` rather than weakening Archive's agent
contract.

Every other core envelope, shape, reference-container, source, contract,
artifact, content, digest, size, and identity term maps one-for-one to its
`arch:` field or controlled-value name. In particular, `artifact`, `entity`,
`claim`, and `assessment` map to Archive-owned classes. `role` is interpreted
as vocabulary, so installed contract fragments define each allowed role term.
The generic `id` and `revision_id` fields remain Archive relations rather than
JSON-LD `@id`: a reference pin and an external source identifier do not have
the same identity semantics.

These mappings are local profiles. They do not assert that an Archive record is
an unconstrained Web Annotation or PROV entity. The PREMIS mapping remains a
separate open deliverable.

The version `1` `archive.transition/operator_initialization` contract has the
closed empty payload `{}`. Its only meaning is in its envelope and references:
exactly one pinned `author`, one pinned `authorizer`, and one pinned `appoints`
reference, all naming the same person revision, with `agent` naming that
person. Its context maps those roles to `arch:author`, `arch:authorizer`, and
`arch:appoints`; they are Archive profiles of PROV association and role, not
aliases that inherit weaker authorization rules.

The Archive program identifier written by version 0.0.1 is
`pkg:hackage/archive@0.0.1.0`. A different installed version writes its own
canonical Package URL. This value identifies the serializer; it never replaces
the asserting `agent`.

This record supersedes only ADR 0012's statement that `by` maps to
`oa:generator` and `agent` maps to `oa:creator`. The rest of ADR 0012 remains
accepted.

## Evidence

The [W3C Web Annotation Recommendation](https://www.w3.org/TR/annotation-vocab/)
lists `as:generator` and `dcterms:creator` for annotations; the Web Annotation
`oa:` namespace contains neither claimed property. [PROV-O](https://www.w3.org/TR/prov-o/)
defines `prov:generatedAtTime`, `prov:Person`, `prov:Organization`, and
`prov:SoftwareAgent`. These standards are evidence for the mappings, while
Archive's stricter meanings remain in its own namespace and profiles.

## Alternatives rejected

- **Keep the two nonexistent `oa:` properties.** The context would claim terms
  the adopted vocabulary does not define.
- **Invent an HTTP namespace.** Archive has no permanent registered domain or
  `w3id.org` allocation; pretending otherwise makes the archive depend on an
  authority it does not have.
- **Use a different random IRI for every term.** One UUID namespace is equally
  non-resolvable and leaves terms readable.
- **Map every field directly to PROV or Web Annotation.** Their contracts are
  weaker and do not express revision pins, Door authority, or Archive's four
  shapes (INV-11).
- **Put operator data in the bootstrap payload.** Author, authorizer, appointed
  person, writer, agent, and recording time already live in the envelope and
  pinned references; duplication could disagree.
- **Leave the payload schema open in code.** The first real record would depend
  on an undocumented choice and could be rejected by its own validator.
