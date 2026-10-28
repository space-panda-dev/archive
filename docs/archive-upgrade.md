# Archive upgrade plan and receipt

[ADR 0126](decisions/0126-fleet-specification-upgrade-boundary.md) divides the
explicit operator workflow between fleet and Archive. Fleet selects a pinned
package, validates its expected release pins, stops writers only during
operator apply, invokes this command, then starts and verifies services when
the receipt permits. This command only reads Archive or proposes the existing
[specification-upgrade transition](decisions/0068-specification-upgrade.md)
through Door. It never deploys or controls services.

Build the self-contained command with `nix build .#upgrade`. Its
`bin/archive-upgrade` pins its Python runtime, Archive, Door, Git, git-annex
and release resources. The tools package also exposes that executable and
`archive-legacy-backfill`; the default Haskell package alone does not expose
these Python commands. `nix build .#deployment` joins the same pinned
Archive and tools, exposing `archive`, `door`, `relay` and `archive-upgrade`
together. Both `.#upgrade` and `.#deployment` publish `passthru.fleetArchive`:
`targetSpecification`, `targetReleaseDigest`, `targetContracts` read from the
public `deployment/release.json`, plus `source` equal to the flake's immutable
store source path. The migration suite checks the descriptor against the
producer's complete release digest computation; fleet need not duplicate it.
A fleet placement must select the released upgrade
package explicitly; an editable checkout is not a deployment pin.

The private JSON configuration contains exactly `schemaVersion` (integer 1),
`archive` (absolute path) and `operator` (active person Entity object id).
Keep it outside this repository. Paths are resolved before hashing. JSON
formatting does not change the semantic configuration digest; changing the
resolved archive path or operator does. No private service configuration or
archival data is bundled with the public command.

```sh
PINNED_PACKAGE/bin/archive-upgrade plan --config PRIVATE_CONFIG \
  --target 0.0.12 --json
PINNED_PACKAGE/bin/archive-upgrade apply --config PRIVATE_CONFIG \
  --target 0.0.12 --plan-digest REVIEWED_PLAN_DIGEST \
  --source-fingerprint REVIEWED_SOURCE_FINGERPRINT --json
```

`plan` emits one JSON object with exactly these fields:

| Field | Meaning |
|---|---|
| `schemaVersion`, `kind` | `1`, `archive-upgrade-plan` |
| `planDigest` | Digest of this plan without the `planDigest` field |
| `sourceFingerprint` | Digest binding the complete source context described below |
| `semanticConfigDigest` | Digest of normalized configuration |
| `targetReleaseDigest` | Digest of the complete packaged target release manifest |
| `targetContracts` | Complete sorted `{id, version, digest}` target catalog |
| `currentSpecification`, `targetSpecification` | Bare versions, such as `0.0.11`, `0.0.12` |
| `status` | `ready`, `noop`, or `blocked` |
| `prerequisites` | `{name, status, required, detail}` rows |

Prerequisite names are `configuration`, `target-release`, `archive-export`,
`operator`, `clean-worktree`, `installed-release`, `door-dry-run`. Their
statuses are `ok`, `missing`, `skipped`; all are required. Ready and no-op
plans have every prerequisite `ok`. For an existing target release, exact
installed bytes replace a new upgrade dry run. A blocked plan exits 1;
ready and no-op exit 0. Failure placeholders in blocked plans are not usable
pins: fleet must reject missing prerequisites and empty contract lists.

All exposed digests are 64 lowercase hexadecimal SHA-256 characters, without
the `sha256:` prefix. Structured values use the adapter's RFC 8785 canonical
JSON; file digests hash exact raw bytes. `targetContracts` sorts by `(id,
version)` and removes only the catalog digest prefix. The release manifest
is `{specification: "archive/0.0.12", files: [{path, sha256}]}` with files
sorted by repository-relative path. It contains the target `VERSION`,
`archive-spec.md`, `context.jsonld`, `contracts.json` and every target contract
bundle under `spec/contracts/`; each bundle's raw bytes and identity are
checked against its catalog pin. Contract digests cover their complete
schemas, contexts and rules, not only their names.

The source fingerprint hashes `{archiveId, head, currentSpecification,
operator, semanticConfigDigest, targetReleaseDigest, targetContracts}`.
`operator` is `{object_id, revision_id}` for the current active person's
head. `head` is the exact Git commit observed by the complete archive export;
the current version is bare. The normalized semantic configuration retains
all three configuration fields. The plan digest covers every public field
except itself, including prerequisite results.

`apply` requires both digests, rechecks the source and authoritative Door
prediction, and submits the identical proposal. Its transition manifest pins
`expected_snapshot` to the reviewed HEAD. Door checks that guard inside its
existing archive write lock before a new transition can commit; an unrelated
concurrent admission therefore cannot silently change the reviewed source. It emits exactly
`schemaVersion: 1`, `kind: archive-upgrade-receipt`, the six pin fields
`planDigest`, `sourceFingerprint`, `semanticConfigDigest`,
`targetReleaseDigest`, `targetContracts`, `targetSpecification`, plus `status`,
`durableMutation`, `detail`. Status is `completed`, `noop`, `refused` or
`failed`. Durable mutation is `none`, `committed` or `unknown`. Completed and
no-op receipts exit 0; refusal and failure exit 1. Malformed CLI arguments
exit 2 before invoking the producer.

A matching original retry validates the deterministic transition identity,
its exact draft and authorizer, its introducing commit's sole parent and the
original plan digests, then asks Door to retry the same transition. It reports
no-op with no durable mutation. A changed source, configuration, release,
operator or proposal cannot silently become a different upgrade. A lost
commit response reports unknown; an error after confirmed commit reports
committed. Neither is a successful service restart instruction. No plan or
receipt file is written into Archive; fleet may retain stdout operationally.
