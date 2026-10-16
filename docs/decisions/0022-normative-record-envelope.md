# 0022. The normative record envelope

Status: accepted

The four record shapes need one closed envelope that every writer and reader
shares. Leaving fields provisional would let provenance disappear at a
serialization boundary or let a writer create a record Door cannot later read.
The predecessor's closed envelope and exact reference pins worked in real
records, but it lacked a separate asserting agent and placed some provenance
and location facts at the wrong layer.

## Decision

Every stored record is a JSON object with exactly these required fields:
`spec`, `revision_id`, `object_id`, `shape`, `by`, `agent`, `at`, `payload`,
`refs`, and `supersedes`. It may also contain `source` and `contracts`. No
other top-level field is allowed.

- `spec` is `archive/<major>.<minor>.<patch>` and identifies the specification
  under which Door admitted the revision.
- `revision_id` is `sha256:` plus 64 lowercase hexadecimal digits, computed as
  P-3 requires.
- `object_id` has the form proposed by ADR 0021.
- `shape` is exactly `artifact`, `entity`, `claim`, or `assessment`.
- `by` is the non-empty portable identifier of the writing program.
- `agent` is the non-empty portable identifier of the person, model, or
  process asserting the record. It is distinct from `by`.
- `at` is Door's recording time, in RFC 3339 UTC form ending in `Z`. It is not
  a capture or event time.
- `payload` is a JSON object validated by the shape and every cited contract.
- `refs` is an array of references, including when empty.
- `supersedes` is an array of exact supersession references, including when
  empty.

A reference is a closed object containing `role`, `id`, and optionally
`revision_id`. All are non-empty strings; `id` is an object identifier. A
`revision_id`, when present, is a canonical revision identifier. Every
`supersedes` reference has role `supersedes`, pins a revision, and names the
same object as the record. Shape and contract rules may require other
references to be pinned.

`source`, when present, is a closed object containing `schema`, `id`, and
optionally `revision_id`. It preserves the identity of imported evidence; it
never supplies the Archive object identity. Its values are non-empty strings,
and `schema` is a portable schema identifier rather than a repository path.

`contracts`, when present, is a non-empty array of closed objects containing
the non-empty strings `id` and `version` and a canonical SHA-256 `digest`.
Every claim cites the contract that defines its payload. Any other shape cites
a contract when its payload depends on one. An empty `contracts` array is
refused: absence says no payload contract is cited, while empty says a citation
container was supplied but contains no citation.

An admission proposal omits `revision_id` and `at`. Door computes and stamps
them. A retry resubmits the same proposal; Door returns the already stored
revision with its original `at`. A caller never chooses recording time. Empty
`refs` and `supersedes` arrays remain required in proposals, because empty is
not absent.

The envelope carries provenance and relations only. Location, currentness,
review state, and policy outcomes are not envelope fields. Meaning particular
to a kind belongs in the cited contract, not in a new top-level field.

## Why this is the smallest envelope

The required fields are precisely the identity, shape, provenance, recording,
content, relation, and lineage facts required by S-8 through S-13. The two
optional fields have meanings that genuinely may be absent: imported source
identity and payload-contract citations outside claims. Closed objects make an
unknown field a refusal rather than a silently discarded fact (INV-24).

The predecessor provides evidence that a closed JSON envelope, explicit empty
arrays, portable contract citations, and exact pins survive migration and
generic readers. ADR 0015 supplies the necessary corrections: `agent` is
separate from `by`, `spec` stays in revision identity, the fourth shape is
`assessment`, and location leaves the record.

## Alternatives rejected

- **Allow extension fields in the envelope.** A reader could silently lose
  their meaning; kinds and contracts already provide the extension point.
- **Combine `by` and `agent`.** It attributes a person's assertion to the
  program that serialized it and violates S-11.
- **Put provenance in payloads.** It duplicates envelope facts and lets kinds
  disagree about attribution (ADR 0003, ADR 0012).
- **Let callers stamp `at`.** It turns an observation supplied by a writer into
  Door's recording time and makes a false chronology authoritative.
- **Treat missing arrays as empty.** It erases whether the writer actually
  supplied the required relation sets and repeats the empty-is-not-absent
  failure.
- **Put location or currentness in the envelope.** Location must not create a
  revision, and currentness is a projection (INV-6, S-18).
