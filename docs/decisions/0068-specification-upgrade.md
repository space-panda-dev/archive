# 0068. An archive upgrades its specification by a transition

Status: accepted

New contracts arrive only in a new specification version (ADR 0057), and an
archive holds exactly the bundles of its own version. "How does an existing
archive migrate between spec versions?" was open, so the only way to use a
new contract was to build a new archive. The shadow could be rebuilt; the
real archive, and any archive taking new recordings, cannot.

## Decision

**An additive release is adopted by a transition.**
`archive.transition/specification_upgrade` version 1 moves an archive from
its current version to a named later version. Its payload has `from` and
`to`, each `archive/<version>`. It pins one `authorizer` Entity revision:
the current revision of the active operator's appointed person, as for
publication (ADR 0051).

**Door admits it only when the later version is additive.** The target's
catalog MUST contain every bundle of the current catalog with identical bytes,
its layout MUST equal the archive's layout, and its core context MUST define
every term the current one defines, with the same meaning. Anything else is
refused as unsupported: such a release needs its own migration decision.

**One commit does everything.** Under the archive lock, `door transition`
verifies each added bundle against the target catalog, installs it under its
digest path, records the new version where the archive records its version,
and writes the transition record citing the upgrade bundle, which is itself
one of the added bundles, as `archive init` does. Expected heads, author, and
authorizer are checked as for every transition, and a complete retry changes
nothing.

**Upgrades are one way.** Records citing a new contract would be unreadable
at the old version, so no downgrade exists.

## Consequences

- The first release to carry this contract (0.0.7) is also the first an
  existing archive can adopt; 0.0.6 archives upgrade with it.
- Upgrading the shadow or the real archive is a command the operator approves
  like any other.
- World and the relay read the archive's new version after the commit like
  any other change.

## Alternatives considered

- **Rebuild the archive at the new version.** Only possible for archives that
  can be regenerated, and it rewrites every record's commit.
- **Let Door accept any bundle a newer catalog lists, without a record.** The
  archive's version would no longer say what it holds, and nothing would
  record who adopted the change.
