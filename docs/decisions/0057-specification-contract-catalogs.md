# 0057. Each specification version lists its contracts

Status: accepted

ADR 0027 rejected "Embed contract digests in Haskell source": the installed
bundle is the authority, and a digest copied into program source creates a
second list that can drift from the first. Door nevertheless carried two such
lists. `AdmissionDryRun` named, for every specification version, the exact
identifier, version, and digest of each contract an archive at that version
must hold. `Bootstrap` named the 0.0.6 set again, with its count and digests.
Adding a contract meant editing three places, and nothing outside Haskell said
which contracts a version installs. A reader or second implementation had to
read Door's source to learn it.

The operator asked on 2026-10-15 for the code and ADR 0027 to agree.

## Decision

**ADR 0027 is right; the source was wrong.** The list moves out of Door and
into the specification distribution.

**Each specification version has a catalog.** `spec/<version>/contracts.json`
is a closed object `{spec, contracts}`. `spec` is `archive/<version>`, and
`contracts` lists each installed bundle exactly once as `{id, version,
digest}`. It is published under CC0 with the rest of the specification
(ADR 0017), and it is a release artifact (ADR 0062). It is not copied into
archives: an archive already holds the bundles themselves under their digest
paths (ADR 0020).

**Door reads the catalog it ships with.** `archive init` installs exactly the
bundles the current version's catalog lists, loading each one by its digest
path and refusing a bundle whose bytes, identifier, or version disagree. When
Door reads an archive, the bundles the archive holds must equal the catalog of
the archive's own specification version. A version with no catalog, or a
catalog Door cannot read, is a refusal, never an empty set (INV-24).

**`make check` keeps the catalogs honest.** Every version directory has a
catalog. Every listed digest names a bundle whose SHA-256, identifier, and
version match the entry, and every bundle in `spec/contracts` is listed by at
least one version.

**A released catalog never changes.** Like the specification text of a
released version, its catalog is frozen; a new contract arrives in a new
specification version's catalog. Archives enforce this in practice: an archive
at an earlier version whose bundles stop matching its catalog becomes
unreadable, and Door's tests read archives at every version.

## Consequences

- Adding a contract to a specification release edits one catalog, not Door.
- A client or second implementation learns which contracts a version
  installs from a published file.
- The catalogs reproduce exactly the sets Door enforced before this change,
  so no existing archive reads differently.

## Alternatives considered

- **Keep the lists in Haskell and amend ADR 0027.** A second list is the
  severed writer/validator chain ADR 0027 named.
- **Derive the catalog from `spec/contracts/` alone.** The directory holds the
  bundles of every version; it cannot say which belong to which.
- **Copy the catalog into each archive.** The archive's bundles already say
  what it holds; a copied catalog would change the archive layout for no new
  information.
